from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from threading import Event

from app.trade_intelligence.opportunity_memory import (
    AsyncOpportunityMemoryWriter,
    EntryAttemptSummary,
    OpportunityMemory,
    OpportunityMemoryState,
    OpportunityTransitionType,
)


UTC = timezone.utc


def t(second: int) -> datetime:
    return datetime(2026, 9, 10, 17, 0, second, tzinfo=UTC)


def test_dbgi_expiry_no_setup_pullback_reclaim_stays_one_opportunity() -> None:
    memory = OpportunityMemory(max_active=10, transition_capacity=64)
    kwargs = dict(
        opportunity_id="DBGI-OPPORTUNITY-1", symbol="DBGI", trading_date=date(2026, 9, 10),
    )
    record = memory.observe(**kwargs, observed_at=t(0), price=Decimal("5.10"))
    assert record.opportunity_state is OpportunityMemoryState.ACTIVE
    memory.observe(**kwargs, observed_at=t(1), price=Decimal("5.40"), qualified=True)
    memory.observe(**kwargs, observed_at=t(2), price=Decimal("5.67"), qualified=True,
                   setup="BULL_FLAG", setup_state="TRIGGERED", entry_anchor=Decimal("5.6728"))
    memory.record_entry_attempt(
        kwargs["trading_date"], kwargs["symbol"], kwargs["opportunity_id"],
        EntryAttemptSummary("DBGI-L1", "BULL_FLAG", t(3), Decimal("5.6728"),
                            Decimal("1207"), Decimal("5.55"), Decimal("0.8"),
                            Decimal("1000000"), Decimal("1207"), "EXPIRED", reason="DEADLINE"),
    )
    memory.observe(**kwargs, observed_at=t(4), price=Decimal("5.89"), qualified=True,
                   setup=None, setup_state=None, blocking_reason="NO_SETUP")
    record = memory.get(**kwargs)
    assert record is not None
    assert record.opportunity_state is OpportunityMemoryState.ACTIVE
    assert record.original_entry_anchor == Decimal("5.6728")
    assert record.highest_price_since_start == Decimal("5.89")
    assert record.latest_entry_result == "EXPIRED"
    memory.observe(**kwargs, observed_at=t(5), price=Decimal("5.67"), qualified=True,
                   setup=None, setup_state=None)
    record = memory.get(**kwargs)
    assert record.post_peak_pullback_low == Decimal("5.67")
    memory.record_reclaim(date(2026, 9, 10), "DBGI", "DBGI-OPPORTUNITY-1", t(6), Decimal("5.80"), confirmed=True)
    assert record.latest_reclaim_level == Decimal("5.80")
    assert [event.kind for event in record.transitions].count(OpportunityTransitionType.DISCOVERED) == 1
    assert len(record.attempts) == 1


def test_identical_observations_do_not_spam_and_ring_is_bounded() -> None:
    memory = OpportunityMemory(max_active=2, transition_capacity=4)
    for i in range(20):
        memory.observe(opportunity_id="A", symbol="AAA", trading_date=date(2026, 9, 10),
                       observed_at=t(0), price=Decimal("1.00"), qualified=True,
                       setup="BULL_FLAG", setup_state="FORMING")
    record = memory.get(date(2026, 9, 10), "AAA", "A")
    assert record is not None and len(record.transitions) <= 4
    assert len(record.transitions) == 3
    memory.observe(opportunity_id="B", symbol="BBB", trading_date=date(2026, 9, 10), observed_at=t(1))
    memory.observe(opportunity_id="C", symbol="CCC", trading_date=date(2026, 9, 10), observed_at=t(2))
    assert len(memory) == 2
    assert memory.get(date(2026, 9, 10), "AAA", "A") is None


def test_explicit_invalidation_is_distinct_from_no_setup() -> None:
    memory = OpportunityMemory()
    day = date(2026, 9, 10)
    memory.observe(opportunity_id="X", symbol="X", trading_date=day, observed_at=t(0),
                   price=Decimal("2"), qualified=True, setup="BULL_FLAG", setup_state="FORMING")
    memory.observe(opportunity_id="X", symbol="X", trading_date=day, observed_at=t(1),
                   price=Decimal("1.9"), blocking_reason="NO_SETUP")
    assert memory.get(day, "X", "X").opportunity_state is OpportunityMemoryState.ACTIVE
    memory.invalidate(day, "X", "X", t(2), "STRUCTURAL_BREAKDOWN")
    assert memory.get(day, "X", "X").opportunity_state is OpportunityMemoryState.INVALIDATED


def test_async_writer_is_bounded_and_surfaces_sink_failure() -> None:
    seen: list[object] = []
    ready = Event()

    def sink(event: object) -> None:
        seen.append(event)
        ready.set()
        raise RuntimeError("test persistence failure")

    writer = AsyncOpportunityMemoryWriter(sink, capacity=1)
    event = object()
    assert writer.submit(event)
    assert ready.wait(1.0)
    writer.close()
    metrics = writer.metrics()
    assert metrics["healthy"] is False
    assert metrics["queue_depth"] == 0
