from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.trade_intelligence.opportunity_memory import (
    EntryAttemptSummary,
    OpportunityMemory,
    OpportunityMemoryState,
)


UTC = timezone.utc
DAY = date(2026, 9, 10)
BASE = datetime(2026, 9, 10, 17, 30, tzinfo=UTC)


def t(seconds: int) -> datetime:
    return BASE + timedelta(seconds=seconds)


def dbgi_memory() -> OpportunityMemory:
    memory = OpportunityMemory()
    memory.observe(opportunity_id="DBGI-1", symbol="DBGI", trading_date=DAY,
                   observed_at=t(0), price=Decimal("5.10"), qualified=True,
                   setup="BULL_FLAG", setup_state="TRIGGERED",
                   entry_anchor=Decimal("5.6728"), hod=Decimal("5.67"))
    memory.observe(opportunity_id="DBGI-1", symbol="DBGI", trading_date=DAY,
                   observed_at=t(10), price=Decimal("5.89"), qualified=True,
                   hod=Decimal("5.89"))
    memory.record_entry_attempt(
        DAY, "DBGI", "DBGI-1",
        EntryAttemptSummary("DBGI-L1", "BULL_FLAG", t(11), Decimal("5.6728"),
                            Decimal("1207"), Decimal("5.50"), Decimal("0.5"),
                            Decimal("1000000"), Decimal("10"), "EXPIRED"),
    )
    memory.observe(opportunity_id="DBGI-1", symbol="DBGI", trading_date=DAY,
                   observed_at=t(20), price=Decimal("5.67"), qualified=True,
                   blocking_reason="NO_SETUP")
    memory.record_reclaim(DAY, "DBGI", "DBGI-1", t(21), Decimal("5.72"))
    memory.record_structure_established(DAY, "DBGI", "DBGI-1", t(22))
    return memory


def assess(memory: OpportunityMemory, **kwargs):
    values = dict(
        entry_anchor=Decimal("5.80"), structural_stop=Decimal("5.67"),
        trigger_price=Decimal("5.80"), live_price=Decimal("5.80"),
        spread_ok=True, liquidity_ok=True, freshness_ok=True,
        reclaim=True,
    )
    values.update(kwargs)
    return memory.assess_live_trigger(DAY, "DBGI", "DBGI-1", **values)


def test_live_trigger_requires_established_pullback_context() -> None:
    memory = OpportunityMemory()
    memory.observe(opportunity_id="SPIKE-1", symbol="SPIKE", trading_date=DAY,
                   observed_at=t(0), price=Decimal("5.10"), qualified=True,
                   setup="BULL_FLAG", setup_state="TRIGGERED",
                   entry_anchor=Decimal("5.60"), hod=Decimal("5.60"))
    result = memory.assess_live_trigger(
        DAY, "SPIKE", "SPIKE-1", entry_anchor=Decimal("5.80"),
        structural_stop=Decimal("5.60"), trigger_price=Decimal("5.80"),
        live_price=Decimal("5.81"), spread_ok=True, liquidity_ok=True,
        freshness_ok=True, reclaim=True,
    )
    assert not result.eligible and result.reason == "NO_ESTABLISHED_STRUCTURE"


def test_dbgI_expired_entry_pullback_reclaim_authorizes_without_extra_bar() -> None:
    memory = dbgi_memory()
    blocked = assess(memory, spread_ok=False, live_price=Decimal("5.80"))
    assert not blocked.eligible and blocked.reason == "SPREAD_BLOCKED"
    ready = assess(memory)
    assert ready.eligible
    assert ready.structure == "RECLAIM"
    assert ready.risk_per_share == Decimal("0.13")
    record = memory.get(DAY, "DBGI", "DBGI-1")
    assert record is not None
    assert record.latest_entry_attempt_price == Decimal("5.6728")
    assert record.opportunity_state is not OpportunityMemoryState.INVALIDATED


def test_stale_quote_and_liquidity_remain_hard_blocks() -> None:
    memory = dbgi_memory()
    assert assess(memory, freshness_ok=False).reason == "STALE_MARKET_DATA"
    assert assess(memory, liquidity_ok=False).reason == "LIQUIDITY_BLOCKED"


def test_live_trigger_cannot_be_below_reclaim_trigger() -> None:
    result = assess(dbgi_memory(), live_price=Decimal("5.79"))
    assert not result.eligible and result.reason == "LIVE_TRIGGER_NOT_REACHED"


def test_duplicate_live_trigger_anchor_is_debounced() -> None:
    memory = dbgi_memory()
    first = assess(memory)
    assert first.eligible
    memory.record_entry_attempt(
        DAY, "DBGI", "DBGI-1",
        EntryAttemptSummary("DBGI-L2", "RECLAIM", t(23), Decimal("5.80"),
                            Decimal("100"), Decimal("5.67"), Decimal("0.4"),
                            Decimal("1000000"), Decimal("0.13"), "AUTHORIZED"),
    )
    second = assess(memory)
    assert not second.eligible and second.reason == "DUPLICATE_LIVE_TRIGGER"


def test_old_anchor_is_never_a_new_structure() -> None:
    result = assess(dbgi_memory(), entry_anchor=Decimal("5.6728"), trigger_price=Decimal("5.6728"), live_price=Decimal("5.6728"))
    assert not result.eligible and result.reason == "OLD_ENTRY_ANCHOR"


def test_higher_low_and_momentum_reacceleration_are_distinct_structures() -> None:
    higher_low = assess(dbgi_memory(), reclaim=False, higher_low=True)
    momentum = assess(dbgi_memory(), reclaim=False, momentum_reaccelerated=True)
    assert higher_low.eligible and higher_low.structure == "HIGHER_LOW_CONTINUATION"
    assert momentum.eligible and momentum.structure == "MOMENTUM_REACCELERATION"


def test_position_and_lifecycle_safety_are_preserved() -> None:
    memory = dbgi_memory()
    assert assess(memory, position_quantity=Decimal("1124")).reason == "POSITION_EXISTS"
    assert assess(memory, lifecycle_count=3).reason == "LIFECYCLE_CAP"


def test_tnon_second_leg_uses_same_opportunity_and_new_anchor() -> None:
    memory = OpportunityMemory()
    memory.observe(opportunity_id="TNON-1", symbol="TNON", trading_date=DAY,
                   observed_at=t(0), price=Decimal("4.90"), qualified=True,
                   setup="BULL_FLAG", setup_state="TRIGGERED",
                   entry_anchor=Decimal("5.20"), hod=Decimal("5.20"))
    memory.observe(opportunity_id="TNON-1", symbol="TNON", trading_date=DAY,
                   observed_at=t(30), price=Decimal("5.60"), qualified=True,
                   hod=Decimal("5.60"))
    memory.observe(opportunity_id="TNON-1", symbol="TNON", trading_date=DAY,
                   observed_at=t(40), price=Decimal("5.48"), qualified=True)
    memory.record_entry_attempt(
        DAY, "TNON", "TNON-1",
        EntryAttemptSummary("TNON-L1", "BULL_FLAG", t(1), Decimal("5.20"),
                            Decimal("200"), Decimal("5.00"), Decimal("0.4"),
                            Decimal("1000000"), Decimal("0.20"), "FILLED"),
    )
    memory.record_structure_established(DAY, "TNON", "TNON-1", t(41))
    result = memory.assess_live_trigger(
        DAY, "TNON", "TNON-1", entry_anchor=Decimal("5.55"),
        structural_stop=Decimal("5.48"), trigger_price=Decimal("5.55"),
        live_price=Decimal("5.56"), spread_ok=True, liquidity_ok=True,
        freshness_ok=True, position_quantity=Decimal("0"), higher_low=True,
        lifecycle_count=1,
    )
    assert result.eligible and result.entry_anchor == Decimal("5.55")
    assert result.entry_anchor != Decimal("5.20")


def test_new_structure_does_not_reset_opportunity_identity_or_history() -> None:
    memory = dbgi_memory()
    before = memory.get(DAY, "DBGI", "DBGI-1")
    result = assess(memory)
    assert result.eligible
    after = memory.get(DAY, "DBGI", "DBGI-1")
    assert before is after
    assert after is not None and after.opportunity_id == "DBGI-1"
    assert after.original_entry_anchor == Decimal("5.6728")
    assert len(after.attempts) == 1


def test_live_trigger_timing_is_recorded_without_tick_history() -> None:
    memory = dbgi_memory()
    assert memory.record_live_trigger(
        DAY, "DBGI", "DBGI-1", t(23), price=Decimal("5.80"),
        anchor=Decimal("5.80"),
    )
    record = memory.get(DAY, "DBGI", "DBGI-1")
    assert record is not None
    assert record.structure_established_at == t(22)
    assert record.live_trigger_first_seen_at == t(23)
    assert len(record.transitions) <= 64
    assert not memory.record_live_trigger(
        DAY, "DBGI", "DBGI-1", t(24), price=Decimal("5.81"),
        anchor=Decimal("5.80"),
    )
