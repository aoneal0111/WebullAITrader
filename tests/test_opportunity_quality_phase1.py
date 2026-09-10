from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.trade_intelligence.opportunity_memory import (
    EntryAttemptSummary,
    OpportunityMemory,
    OpportunityQuality,
)


UTC = timezone.utc
DAY = date(2026, 9, 10)
START = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
D = Decimal


def _memory(*, peak: str = "10.50") -> OpportunityMemory:
    memory = OpportunityMemory()
    memory.observe(
        opportunity_id="OPP", symbol="XYZ", trading_date=DAY,
        observed_at=START, price=D("10.00"), qualified=True,
        setup="FLAT_TOP_BREAKOUT", setup_state="TRIGGERED",
        entry_anchor=D("10.10"), hod=D("10.10"),
    )
    memory.observe(
        opportunity_id="OPP", symbol="XYZ", trading_date=DAY,
        observed_at=START + timedelta(minutes=5), price=D(peak),
        qualified=True, hod=D(peak),
    )
    memory.observe(
        opportunity_id="OPP", symbol="XYZ", trading_date=DAY,
        observed_at=START + timedelta(minutes=7), price=D("10.25"),
        qualified=True,
    )
    return memory


def _attempt(memory: OpportunityMemory, *, at: datetime = START + timedelta(minutes=8)):
    memory.record_entry_attempt(
        DAY, "XYZ", "OPP",
        EntryAttemptSummary(
            "L1", "FLAT_TOP_BREAKOUT", at, D("10.10"), D("100"),
            D("10.00"), D("0.5"), D("3000000"), D("0.10"), "EXPIRED",
            reason="ENTRY_STALE",
        ),
    )


def test_early_continuation_is_strong_or_acceptable():
    memory = _memory(peak="10.50")
    result = memory.assess_quality(
        DAY, "XYZ", "OPP", proposed_entry=D("10.30"),
        structural_stop=D("10.00"), evaluated_at=START + timedelta(minutes=10),
        session="REGULAR", reward_reference=D("11.00"), lifecycle_count=1,
    )
    assert result.classification in {OpportunityQuality.STRONG, OpportunityQuality.ACCEPTABLE}
    assert result.authorization_allowed is True
    assert result.remaining_reward == D("0.70")
    assert result.reward_risk_ratio > D("1.25")


def test_one_expired_lifecycle_does_not_equal_exhaustion():
    memory = _memory(peak="10.50")
    _attempt(memory)
    result = memory.assess_quality(
        DAY, "XYZ", "OPP", proposed_entry=D("10.30"),
        structural_stop=D("10.00"), evaluated_at=START + timedelta(minutes=12),
        session="AFTER_HOURS", reward_reference=D("11.00"), lifecycle_count=1,
    )
    assert result.classification is not OpportunityQuality.EXHAUSTED
    assert result.authorization_allowed is True


def test_dbgi_style_late_expansion_is_exhausted_for_weak_remaining_reward():
    memory = _memory(peak="17.48")
    _attempt(memory)
    result = memory.assess_quality(
        DAY, "XYZ", "OPP", proposed_entry=D("17.4137"),
        structural_stop=D("17.24"),
        evaluated_at=START + timedelta(minutes=90),
        session="AFTER_HOURS", reward_reference=D("17.48"), lifecycle_count=1,
    )
    assert result.classification is OpportunityQuality.EXHAUSTED
    assert result.authorization_allowed is False
    assert "PRIOR_EXPANSION_CONSUMED" in result.reasons
    assert "OPPORTUNITY_AGE_DECAY" in result.reasons
    assert "REMAINING_REWARD_WEAK" in result.reasons


def test_quality_is_attached_to_opportunity_and_survives_new_lifecycle():
    memory = _memory(peak="17.48")
    result = memory.assess_quality(
        DAY, "XYZ", "OPP", proposed_entry=D("17.4137"),
        structural_stop=D("17.24"), evaluated_at=START + timedelta(minutes=90),
        session="AFTER_HOURS", reward_reference=D("17.48"), lifecycle_count=1,
    )
    record = memory.get(DAY, "XYZ", "OPP")
    assert record is not None
    assert record.quality_classification is result.classification
    assert record.quality_evaluated_at == result.evaluated_at
    memory.record_entry_attempt(
        DAY, "XYZ", "OPP",
        EntryAttemptSummary(
            "L2", "FLAT_TOP_BREAKOUT", START + timedelta(minutes=91),
            D("17.4137"), D("100"), D("17.24"), D("0.5"), D("3000000"),
            D("0.1737"), "BLOCKED", reason="QUALITY_EXHAUSTED",
        ),
    )
    assert memory.get(DAY, "XYZ", "OPP").quality_classification is result.classification


def test_vwap_and_session_context_are_reported_without_changing_hard_gates():
    memory = _memory(peak="10.50")
    result = memory.assess_quality(
        DAY, "XYZ", "OPP", proposed_entry=D("10.30"),
        structural_stop=D("10.00"), evaluated_at=START + timedelta(minutes=10),
        session="AFTER_HOURS", vwap=D("9.50"), reward_reference=D("11.00"),
        session_end_at=START + timedelta(minutes=30), lifecycle_count=1,
    )
    assert result.vwap_extension_percent == D("8.421052631578947368421052632")
    assert "LATE_SESSION_DECAY" not in result.reasons
    assert result.authorization_allowed is True

