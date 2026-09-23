from datetime import UTC, datetime
from decimal import Decimal

from app.momentum_scanner.models import (
    AssetClass,
    CatalystStatus,
    CatalystType,
    FloatProvenance,
    ScannerObservation,
)
from app.momentum_scanner.rules import evaluate_candidate
from app.realtime_scanner.engine import RealtimeScannerEngine
from app.strategies.warrior_momentum.models import (
    CandidateStatus,
    MomentumCandidate,
    MomentumScore,
    ReasonCode,
    SetupDetection,
    SetupState,
    SetupType,
    StopModel,
)
from app.strategies.warrior_momentum.runtime import (
    WarriorMomentumRuntime,
    entry_rejections,
)


NOW = datetime(2026, 9, 23, 15, 0, tzinfo=UTC)


def _observation(*, spread: Decimal = Decimal("5"), halted: bool = False):
    return ScannerObservation(
        symbol="TEST",
        timestamp=NOW,
        price=Decimal("5"),
        previous_close=Decimal("4"),
        current_volume=Decimal("500000"),
        average_30_day_volume=Decimal("25000"),
        float_shares=Decimal("1000000"),
        bid=Decimal("4.75"),
        ask=Decimal("5") + (Decimal("5") * spread / Decimal("100")),
        catalyst=CatalystType.NONE,
        catalyst_headline=None,
        tradable=True,
        halted=halted,
        asset_class=AssetClass.STOCK,
        catalyst_status=CatalystStatus.FALSE,
        float_provenance=FloatProvenance.AUTHORITATIVE_FLOAT,
    )


def _candidate(*, spread: Decimal = Decimal("5"), setup_state=SetupState.TRIGGERED):
    setup = SetupDetection(
        SetupType.BULL_FLAG,
        setup_state,
        Decimal("80"),
        trigger=Decimal("5"),
        stop_price=Decimal("4.50"),
        stop_model=StopModel.FLAG_LOW,
    )
    return MomentumCandidate(
        rank=1,
        symbol="TEST",
        timestamp=NOW,
        price=Decimal("5"),
        percentage_change=Decimal("25"),
        relative_volume=Decimal("20"),
        float_shares=Decimal("1000000"),
        volume=Decimal("500000"),
        dollar_volume=Decimal("2500000"),
        spread_percent=spread,
        catalyst_status=CatalystStatus.FALSE,
        catalyst_type=CatalystType.NONE,
        score=MomentumScore(Decimal("90"), ()),
        stocks_in_play=(),
        setup=setup,
        session="REGULAR",
        status=CandidateStatus.QUALIFIED,
        tradable=True,
        halted=False,
        distance_from_hod_percent=Decimal("0"),
        reason_codes=(),
        explanations=(),
        discovery_qualified=True,
        bid=Decimal("4.75"),
        ask=Decimal("5"),
    )


def test_execution_spread_failure_remains_observation_eligible():
    decision = evaluate_candidate(_observation())

    assert decision.qualified is False
    assert decision.observation_eligible is True
    assert decision.observation_failed_rules == ()
    assert "spread" in decision.failed_rules

    runtime = WarriorMomentumRuntime()
    candidate = runtime.discover(_observation(), (), session="REGULAR")
    assert candidate.observation_eligible is True
    assert ReasonCode.SPREAD_WIDE in candidate.reason_codes
    assert ReasonCode.SPREAD_WIDE not in candidate.observation_blockers


def test_execution_gates_remain_fail_closed_for_retained_observation():
    candidate = _candidate()
    reasons = entry_rejections(candidate, WarriorMomentumRuntime().config)

    assert ReasonCode.SPREAD_WIDE in reasons
    assert candidate.observation_eligible is True


def test_no_setup_is_not_an_observation_blocker():
    candidate = _candidate(setup_state=SetupState.FORMING)
    reasons = entry_rejections(candidate, WarriorMomentumRuntime().config)

    assert ReasonCode.NO_SETUP in reasons
    assert candidate.observation_eligible is True
    assert candidate.setup is not None
    assert candidate.setup.state is SetupState.FORMING


def test_hard_observation_failure_is_explicit():
    decision = evaluate_candidate(_observation(spread=Decimal("0.5"), halted=True))

    assert decision.observation_eligible is False
    assert "not_halted" in decision.observation_failed_rules


def test_engine_retains_observation_eligible_decision_even_when_unqualified():
    engine = RealtimeScannerEngine(object(), object(), object())
    engine._active_symbols = {"TEST"}
    decision = evaluate_candidate(_observation())

    engine._record_decision(decision)

    assert "TEST" in engine._recent_interest
