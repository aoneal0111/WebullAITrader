from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.strategies.warrior_momentum.autonomous_paper import lifecycle_identity
from app.trade_intelligence.opportunity_memory import (
    EntryAttemptSummary,
    OpportunityMemory,
    OpportunityMemoryState,
    OpportunityTransitionType,
)


UTC = timezone.utc
DAY = date(2026, 9, 10)


def at(second: int) -> datetime:
    return datetime(2026, 9, 10, 17, 30, second, tzinfo=UTC)


def seeded() -> OpportunityMemory:
    memory = OpportunityMemory()
    memory.observe(opportunity_id="DBGI-1", symbol="DBGI", trading_date=DAY,
                   observed_at=at(0), price=Decimal("5.10"), qualified=True,
                   setup="BULL_FLAG", setup_state="TRIGGERED",
                   entry_anchor=Decimal("5.6728"), hod=Decimal("5.70"))
    memory.record_entry_attempt(
        DAY, "DBGI", "DBGI-1",
        EntryAttemptSummary("L1", "BULL_FLAG", at(1), Decimal("5.6728"),
                            Decimal("1207"), Decimal("5.50"), Decimal("0.5"),
                            Decimal("1000000"), Decimal("10"), "EXPIRED"),
    )
    memory.observe(opportunity_id="DBGI-1", symbol="DBGI", trading_date=DAY,
                   observed_at=at(2), price=Decimal("5.89"), qualified=True)
    memory.observe(opportunity_id="DBGI-1", symbol="DBGI", trading_date=DAY,
                   observed_at=at(3), price=Decimal("5.67"), qualified=True,
                   blocking_reason="NO_SETUP")
    memory.observe(opportunity_id="DBGI-1", symbol="DBGI", trading_date=DAY,
                   observed_at=at(4), price=Decimal("5.80"), qualified=True,
                   setup="BULL_FLAG", setup_state="TRIGGERED",
                   entry_anchor=Decimal("5.80"))
    memory.record_reclaim(DAY, "DBGI", "DBGI-1", at(4), Decimal("5.80"), confirmed=True)
    return memory


def test_expired_entry_and_no_setup_leave_opportunity_active() -> None:
    record = seeded().get(DAY, "DBGI", "DBGI-1")
    assert record is not None
    assert record.opportunity_state is OpportunityMemoryState.ACTIONABLE
    assert record.original_entry_anchor == Decimal("5.6728")
    assert record.latest_entry_result == "EXPIRED"
    assert record.latest_blocking_reason == "NO_SETUP"
    assert record.post_peak_pullback_low == Decimal("5.67")
    assert record.latest_reclaim_level == Decimal("5.80")


def test_new_structure_requires_new_anchor_and_structural_evidence() -> None:
    memory = seeded()
    allowed = memory.assess_new_structure(
        DAY, "DBGI", "DBGI-1", entry_anchor=Decimal("5.80"),
        structural_stop=Decimal("5.67"), spread_ok=True, liquidity_ok=True,
        freshness_ok=True, higher_low=True,
    )
    assert allowed.eligible
    assert allowed.structure == "HIGHER_LOW_CONTINUATION"
    assert allowed.risk_per_share == Decimal("0.13")
    old = memory.assess_new_structure(
        DAY, "DBGI", "DBGI-1", entry_anchor=Decimal("5.6728"),
        structural_stop=Decimal("5.50"), spread_ok=True, liquidity_ok=True,
        freshness_ok=True, higher_low=True,
    )
    assert not old.eligible and old.reason == "OLD_ENTRY_ANCHOR"


def test_wide_spread_blocks_but_recovery_can_be_reassessed() -> None:
    memory = seeded()
    blocked = memory.assess_new_structure(
        DAY, "DBGI", "DBGI-1", entry_anchor=Decimal("5.80"),
        structural_stop=Decimal("5.67"), spread_ok=False, liquidity_ok=True,
        freshness_ok=True, reclaim=True,
    )
    assert not blocked.eligible and blocked.reason == "SPREAD_BLOCKED"
    recovered = memory.assess_new_structure(
        DAY, "DBGI", "DBGI-1", entry_anchor=Decimal("5.80"),
        structural_stop=Decimal("5.67"), spread_ok=True, liquidity_ok=True,
        freshness_ok=True, reclaim=True,
    )
    assert recovered.eligible and recovered.structure == "RECLAIM"


def test_new_structure_preserves_lifecycle_cap_and_position_safety() -> None:
    memory = seeded()
    capped = memory.assess_new_structure(
        DAY, "DBGI", "DBGI-1", entry_anchor=Decimal("5.80"),
        structural_stop=Decimal("5.67"), spread_ok=True, liquidity_ok=True,
        freshness_ok=True, reclaim=True, lifecycle_count=3, max_lifecycles=3,
    )
    assert not capped.eligible and capped.reason == "LIFECYCLE_CAP"
    positioned = memory.assess_new_structure(
        DAY, "DBGI", "DBGI-1", entry_anchor=Decimal("5.80"),
        structural_stop=Decimal("5.67"), spread_ok=True, liquidity_ok=True,
        freshness_ok=True, reclaim=True, position_quantity=Decimal("1"),
    )
    assert not positioned.eligible and positioned.reason == "POSITION_EXISTS"


def test_new_lifecycle_identity_differs_without_resetting_opportunity() -> None:
    first = type("Signal", (), {"strategy_id": "WARRIOR_MOMENTUM_V1", "symbol": "DBGI",
                                "timestamp": at(1), "setup_type": "BULL_FLAG",
                                "entry_trigger": Decimal("5.6728"), "stop_price": Decimal("5.50")})()
    second = type("Signal", (), {"strategy_id": "WARRIOR_MOMENTUM_V1", "symbol": "DBGI",
                                 "timestamp": at(4), "setup_type": "BULL_FLAG",
                                 "entry_trigger": Decimal("5.80"), "stop_price": Decimal("5.67")})()
    assert lifecycle_identity(first) != lifecycle_identity(second)
    record = seeded().get(DAY, "DBGI", "DBGI-1")
    assert record is not None and record.opportunity_id == "DBGI-1"


def test_no_structure_or_freshness_never_authorizes() -> None:
    memory = seeded()
    for kwargs in (
        {"spread_ok": True, "liquidity_ok": True, "freshness_ok": False, "reclaim": True},
        {"spread_ok": True, "liquidity_ok": False, "freshness_ok": True, "reclaim": True},
        {"spread_ok": True, "liquidity_ok": True, "freshness_ok": True},
    ):
        result = memory.assess_new_structure(
            DAY, "DBGI", "DBGI-1", entry_anchor=Decimal("5.80"),
            structural_stop=Decimal("5.67"), **kwargs,
        )
        assert not result.eligible
