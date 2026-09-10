from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.trade_intelligence.opportunity_memory import (
    EntryAttemptSummary,
    OpportunityMemory,
    OpportunityMemoryState,
    PullbackClassification,
)
from app.momentum_scanner.models import (
    AssetClass, CatalystStatus, CatalystType, ScannerObservation,
)
from app.strategies.warrior_momentum.forward_models import (
    FloatProvenance, ForwardCaptureConfiguration, PaperAccountContext,
    PointInTimeObservation,
)
from app.strategies.warrior_momentum.forward_runtime import WarriorForwardCaptureService
from app.strategies.warrior_momentum.forward_store import ForwardCaptureStore
from app.strategies.warrior_momentum.forward_queue import ForwardCaptureWriter
from app.strategies.warrior_momentum.models import (
    CandidateStatus, MinuteBar, MomentumCandidate, MomentumEntrySignal,
    MomentumScore, SetupDetection, SetupState, SetupType, StopModel,
)


UTC = timezone.utc
DAY = date(2026, 9, 10)
BASE = datetime(2026, 9, 10, 19, 0, tzinfo=UTC)


def at(seconds: int) -> datetime:
    return BASE + timedelta(seconds=seconds)


def memory_with_expired_entry() -> OpportunityMemory:
    memory = OpportunityMemory()
    memory.observe(
        opportunity_id="DBGI-OPP", symbol="DBGI", trading_date=DAY,
        observed_at=at(0), price=Decimal("5.10"), qualified=True,
        setup="HIGH_OF_DAY_BREAKOUT", setup_state="TRIGGERED",
        entry_anchor=Decimal("5.6728"), hod=Decimal("5.6728"),
    )
    memory.observe(
        opportunity_id="DBGI-OPP", symbol="DBGI", trading_date=DAY,
        observed_at=at(30), price=Decimal("7.48"), qualified=True,
        hod=Decimal("7.48"),
    )
    memory.observe(
        opportunity_id="DBGI-OPP", symbol="DBGI", trading_date=DAY,
        observed_at=at(60), price=Decimal("6.67"), qualified=True,
        blocking_reason="SPREAD_WIDE",
    )
    memory.record_entry_attempt(
        DAY, "DBGI", "DBGI-OPP",
        EntryAttemptSummary(
            "DBGI-L1", "HIGH_OF_DAY_BREAKOUT", at(1), Decimal("5.6728"),
            Decimal("1207"), Decimal("5.59"), Decimal("2.27"),
            Decimal("3000000"), Decimal("0.0828"), "EXPIRED",
            reason="ENTRY_STALE",
        ),
    )
    return memory


def assess(memory: OpportunityMemory, **overrides):
    values = dict(
        entry_anchor=Decimal("6.93"), structural_stop=Decimal("6.67"),
        spread_ok=True, liquidity_ok=True, freshness_ok=True,
        classification=PullbackClassification.HEALTHY,
        structure="HIGHER_LOW_CONTINUATION", structure_established=True,
        reclaim_level=Decimal("6.93"), lifecycle_count=1,
    )
    values.update(overrides)
    return memory.assess_continuation_structure(
        DAY, "DBGI", "DBGI-OPP", **values,
    )


def test_expired_entry_keeps_opportunity_and_old_thesis_is_not_reused():
    memory = memory_with_expired_entry()
    record = memory.get(DAY, "DBGI", "DBGI-OPP")
    assert record is not None
    assert record.opportunity_state is not OpportunityMemoryState.INVALIDATED
    assert record.original_entry_anchor == Decimal("5.6728")
    assert assess(memory).entry_anchor == Decimal("6.93")
    assert assess(
        memory, entry_anchor=Decimal("5.6728"), structural_stop=Decimal("5.50"),
    ).reason == "OLD_ENTRY_ANCHOR"


def test_healthy_structure_is_eligible_after_completed_context():
    result = assess(memory_with_expired_entry())
    assert result.eligible
    assert result.structure == "HIGHER_LOW_CONTINUATION"
    assert result.structural_stop == Decimal("6.67")
    assert result.risk_per_share == Decimal("0.26")


def test_spread_recovery_is_reassessable_without_new_bar():
    memory = memory_with_expired_entry()
    blocked = assess(memory, spread_ok=False)
    recovered = assess(memory, spread_ok=True)
    assert not blocked.eligible and blocked.reason == "SPREAD_BLOCKED"
    assert recovered.eligible


def test_liquidity_recovery_is_reassessable_without_losing_context():
    memory = memory_with_expired_entry()
    assert assess(memory, liquidity_ok=False).reason == "LIQUIDITY_BLOCKED"
    assert assess(memory, liquidity_ok=True).eligible


def test_failed_pullback_cannot_authorize():
    result = assess(
        memory_with_expired_entry(),
        classification=PullbackClassification.FAILED,
    )
    assert not result.eligible and result.reason == "PULLBACK_FAILED"


def test_single_spike_without_pullback_context_cannot_authorize():
    memory = OpportunityMemory()
    memory.observe(
        opportunity_id="SPIKE", symbol="SPIKE", trading_date=DAY,
        observed_at=at(0), price=Decimal("5.00"), qualified=True,
        entry_anchor=Decimal("5.10"),
    )
    result = memory.assess_continuation_structure(
        DAY, "SPIKE", "SPIKE", entry_anchor=Decimal("5.20"),
        structural_stop=Decimal("5.10"), spread_ok=True, liquidity_ok=True,
        freshness_ok=True, classification=PullbackClassification.HEALTHY,
        structure="RECLAIM", structure_established=True,
    )
    assert not result.eligible and result.reason == "NO_PULLBACK_CONTEXT"


def test_new_structure_has_fresh_stop_and_risk_not_old_entry_geometry():
    result = assess(memory_with_expired_entry(), entry_anchor=Decimal("7.03"),
                    structural_stop=Decimal("6.67"), structure="RECLAIM")
    assert result.eligible
    assert result.entry_anchor == Decimal("7.03")
    assert result.risk_per_share == Decimal("0.36")
    assert result.entry_anchor != Decimal("5.6728")


def test_duplicate_trigger_anchor_is_bounded_by_existing_attempts():
    memory = memory_with_expired_entry()
    memory.record_entry_attempt(
        DAY, "DBGI", "DBGI-OPP",
        EntryAttemptSummary(
            "DBGI-L2", "RECLAIM", at(70), Decimal("6.93"), Decimal("300"),
            Decimal("6.67"), Decimal("0.5"), Decimal("3000000"),
            Decimal("0.26"), "NEW_STRUCTURAL_ENTRY",
        ),
    )
    assert len(memory.get(DAY, "DBGI", "DBGI-OPP").attempts) == 2
    assert assess(memory, lifecycle_count=2).eligible


def test_lifecycle_cap_blocks_fourth_attempt():
    assert assess(memory_with_expired_entry(), lifecycle_count=3).reason == "LIFECYCLE_CAP"


def test_position_authority_blocks_flat_reentry():
    assert assess(memory_with_expired_entry(), position_quantity=Decimal("42")).reason == "POSITION_EXISTS"


def test_no_setup_does_not_erase_impulse_or_pullback():
    memory = memory_with_expired_entry()
    memory.observe(
        opportunity_id="DBGI-OPP", symbol="DBGI", trading_date=DAY,
        observed_at=at(65), price=Decimal("6.70"), qualified=True,
        setup=None, setup_state=None, blocking_reason="NO_SETUP",
    )
    record = memory.get(DAY, "DBGI", "DBGI-OPP")
    assert record is not None
    assert record.highest_price_since_start == Decimal("7.48")
    assert record.post_peak_pullback_low == Decimal("6.67")
    assert record.original_entry_anchor == Decimal("5.6728")


def test_ring_and_meaningful_transition_history_remain_bounded():
    memory = OpportunityMemory(transition_capacity=4)
    for index in range(40):
        memory.observe(
            opportunity_id="BOUNDED", symbol="DBGI", trading_date=DAY,
            observed_at=at(index), price=Decimal("5") + Decimal(index) / 100,
            qualified=True, setup_state="FORMING",
        )
    record = memory.get(DAY, "DBGI", "BOUNDED")
    assert record is not None
    assert len(record.transitions) <= 4
    assert len(memory.records()) == 1


def test_production_observe_routes_recovered_candidate_through_live_seam(tmp_path):
    timestamp = at(120)
    setup = SetupDetection(
        SetupType.FLAT_TOP_BREAKOUT, SetupState.TRIGGERED, Decimal("86"),
        Decimal("10.20"), Decimal("10.00"), StopModel.BREAKOUT_LEVEL,
        Decimal("10.19"),
    )
    candidate = MomentumCandidate(
        rank=1, symbol="DBGI", timestamp=timestamp, price=Decimal("10.21"),
        percentage_change=Decimal("20"), relative_volume=Decimal("10"),
        float_shares=Decimal("1000000"), volume=Decimal("1000000"),
        dollar_volume=Decimal("3000000"), spread_percent=Decimal("0.90"),
        catalyst_status=CatalystStatus.TRUE, catalyst_type=CatalystType.EARNINGS,
        score=MomentumScore(Decimal("80"), ()), stocks_in_play=(), setup=setup,
        session="REGULAR", status=CandidateStatus.ENTRY_READY, tradable=True,
        halted=False, distance_from_hod_percent=Decimal("1"), reason_codes=(),
        explanations=(), discovery_qualified=True, bid=Decimal("10.20"),
        ask=Decimal("10.21"),
    )
    signal = MomentumEntrySignal(
        strategy_id="WARRIOR_MOMENTUM_V1", symbol="DBGI", timestamp=timestamp,
        session="REGULAR", momentum_score=Decimal("80"),
        setup_type=SetupType.FLAT_TOP_BREAKOUT, entry_trigger=Decimal("10.20"),
        reference_price=Decimal("10.21"), stop_price=Decimal("10.00"),
        stop_model=StopModel.BREAKOUT_LEVEL, risk_per_share=Decimal("0.20"),
        target_levels=(Decimal("10.40"), Decimal("10.60"), Decimal("10.80")),
        catalyst_state=CatalystStatus.TRUE, relative_volume=Decimal("10"),
        float_shares=Decimal("1000000"), spread_percent=Decimal("0.90"),
        volume=Decimal("1000000"), dollar_volume=Decimal("3000000"),
        setup_score=Decimal("86"), reasoning_codes=(),
    )
    submissions = []
    store = ForwardCaptureStore(tmp_path / "phase4b.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer,
        capture_config=ForwardCaptureConfiguration(
            storage_path=tmp_path / "phase4b.sqlite3",
        ),
        paper_entry_submitter=lambda submitted, shares, risk: submissions.append(
            (submitted, shares, risk)
        ) or True,
    )
    try:
        seam_calls = []
        original_recovered = service.authorize_recovered_continuation
        service.authorize_recovered_continuation = lambda *args, **kwargs: (
            seam_calls.append((args, kwargs)) or original_recovered(*args, **kwargs)
        )
        service._memory_opportunity_ids["DBGI"] = "DBGI-OPP"
        service.opportunity_memory.observe(
            opportunity_id="DBGI-OPP", symbol="DBGI", trading_date=DAY,
            observed_at=at(0), price=Decimal("5.67"), qualified=True,
            entry_anchor=Decimal("5.67"),
        )
        service.opportunity_memory.observe(
            opportunity_id="DBGI-OPP", symbol="DBGI", trading_date=DAY,
            observed_at=at(30), price=Decimal("7.48"), qualified=True,
        )
        service.opportunity_memory.observe(
            opportunity_id="DBGI-OPP", symbol="DBGI", trading_date=DAY,
            observed_at=at(60), price=Decimal("6.67"), qualified=True,
        )
        service.opportunity_memory.record_entry_attempt(
            DAY, "DBGI", "DBGI-OPP",
            EntryAttemptSummary(
                "DBGI-L1", "HIGH_OF_DAY_BREAKOUT", at(1), Decimal("5.67"),
                Decimal("1207"), Decimal("5.59"), Decimal("2.27"),
                Decimal("3000000"), Decimal("0.08"), "EXPIRED",
                reason="ENTRY_STALE",
            ),
        )
        candidates = {
            "value": replace(candidate, spread_percent=Decimal("2.00")),
        }
        service.runtime.discover = lambda observation, bars, session: candidates["value"]
        service.runtime.assess_entry = lambda discovered: (
            discovered, None if discovered.spread_percent > Decimal("1.25") else signal
        )
        service.runtime.technical_entry_signal = lambda discovered: signal
        bars = tuple(MinuteBar(
            "DBGI", at(index * 60), Decimal("9.90"), Decimal("10.00"),
            Decimal("9.80"), Decimal("9.95"), Decimal("1000"),
        ) for index in range(5))
        observation = PointInTimeObservation(
            observation=ScannerObservation(
                symbol="DBGI", timestamp=timestamp, price=Decimal("10.21"),
                previous_close=Decimal("8"), current_volume=Decimal("1000000"),
                average_30_day_volume=Decimal("100000"), float_shares=Decimal("1000000"),
                bid=Decimal("10.20"), ask=Decimal("10.21"),
                catalyst=CatalystType.EARNINGS, catalyst_headline="earnings",
                tradable=True, halted=False, asset_class=AssetClass.STOCK,
                catalyst_status=CatalystStatus.TRUE,
            ),
            session="REGULAR", bars=bars, float_provenance=FloatProvenance.UNKNOWN,
            quote_freshness_seconds=Decimal("0.1"), last_price_freshness_seconds=Decimal("0.1"),
        )
        account = PaperAccountContext(Decimal("50000"), Decimal("25000"), frozenset({"DBGI"}))
        # First observation is an execution-quality block; it must not submit.
        service.runtime.assess_entry = lambda discovered: (discovered, None)
        service.observe(observation, account=account)
        assert not submissions
        assert len(seam_calls) == 1
        candidates["value"] = replace(candidate, spread_percent=Decimal("0.90"))
        service.runtime.assess_entry = lambda discovered: (discovered, signal)
        service.observe(observation, account=account)
        assert len(seam_calls) == 2
        assert len(submissions) == 1
        assert submissions[0][0].entry_trigger == Decimal("10.20")
        assert submissions[0][1] > 0
        record = service.opportunity_memory.get(DAY, "DBGI", "DBGI-OPP")
        assert record is not None
        assert record.live_trigger_first_seen_at is not None
        assert len(record.attempts) == 2
    finally:
        writer.close()
