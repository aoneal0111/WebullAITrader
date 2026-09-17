from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from app.momentum_scanner.models import CatalystStatus, CatalystType
from app.momentum_scanner.models import AssetClass, ScannerObservation
from app.strategies.warrior_momentum import (
    AdaptiveDecision, AdaptiveReason, MomentumCandidate, MomentumScore,
    SetupDetection, SetupState, SetupType, StopModel, WarriorAdaptiveContext,
    WarriorMomentumConfig, WarriorMomentumRuntime, ReasonCode,
    warrior_observation_eligible,
)
from app.momentum_scanner import evaluate_candidate


def candidate(*, symbol="XYZ", rvol="2", move="80", dollar="12000000", spread="1.8",
              setup=True, reasons=()):
    setup_value = SetupDetection(SetupType.HIGH_OF_DAY_BREAKOUT, SetupState.TRIGGERED,
                                 Decimal("80"), Decimal("10"), Decimal("9.8"), StopModel.RECENT_SWING_LOW) if setup else None
    return MomentumCandidate(
        rank=0, symbol=symbol, timestamp=datetime(2026, 9, 17, 14, 0, tzinfo=UTC),
        price=Decimal("10"), percentage_change=Decimal(move), relative_volume=Decimal(rvol),
        float_shares=Decimal("1000000"), volume=Decimal("1200000"), dollar_volume=Decimal(dollar),
        spread_percent=Decimal(spread), catalyst_status=CatalystStatus.TRUE,
        catalyst_type=CatalystType.EARNINGS, score=MomentumScore(Decimal("85"), ()),
        stocks_in_play=(), setup=setup_value, session="REGULAR", status="QUALIFIED",
        tradable=True, halted=False, distance_from_hod_percent=Decimal("0"),
        reason_codes=tuple(reasons), explanations=(), discovery_qualified=True,
    )


def test_exceptional_mover_with_lower_rvol_is_contextually_supported():
    result = WarriorAdaptiveContext().evaluate(candidate())
    assert result.decision is AdaptiveDecision.READY
    assert AdaptiveReason.RVOL_BELOW_TYPICAL_BUT_SUPPORTED in result.reasons


def test_same_rvol_weak_mover_is_not_accepted():
    result = WarriorAdaptiveContext().evaluate(candidate(move="8", dollar="150000", spread="1.0", setup=False))
    assert result.decision in {AdaptiveDecision.REJECT, AdaptiveDecision.WATCH}
    assert result.decision is not AdaptiveDecision.READY


def test_catastrophic_spread_and_stale_data_are_hard_blocks():
    context = WarriorAdaptiveContext()
    assert context.evaluate(candidate(spread="5")).decision is AdaptiveDecision.REJECT
    assert context.evaluate(candidate(reasons=(ReasonCode.STALE_MARKET_DATA,))).decision is AdaptiveDecision.REJECT


def test_repeated_observations_track_improvement_and_are_bounded():
    context = WarriorAdaptiveContext(max_symbols=2)
    first = context.evaluate(candidate(symbol="A", move="8", dollar="300000", setup=False))
    second = context.evaluate(candidate(symbol="A", move="80", dollar="12000000"))
    assert second.observation_count == first.observation_count + 1
    assert AdaptiveReason.PARTICIPATION_IMPROVING in second.reasons
    context.evaluate(candidate(symbol="B")); context.evaluate(candidate(symbol="C"))
    assert context.symbol_state_count <= 2


def test_disabled_runtime_preserves_legacy_rejections():
    value = candidate(rvol="1", spread="2", reasons=(ReasonCode.RVOL_LOW, ReasonCode.SPREAD_WIDE))
    legacy = WarriorMomentumRuntime().assess_entry(value)
    adaptive = WarriorMomentumRuntime(WarriorMomentumConfig(adaptive_context_enabled=True)).assess_entry(value)
    assert legacy[1] is None
    assert adaptive[0].status.value in {"ENTRY_READY", "SETUP_FORMING", "INELIGIBLE_FOR_EXECUTION", "QUALIFIED"}


def test_enabled_runtime_does_not_reject_exceptional_move_for_rvol_or_spread_alone():
    value = candidate(rvol="1.8", spread="1.8", reasons=(ReasonCode.RVOL_LOW, ReasonCode.SPREAD_WIDE))
    assessed, signal = WarriorMomentumRuntime(WarriorMomentumConfig(adaptive_context_enabled=True)).assess_entry(value)
    assert assessed.status.value == "ENTRY_READY"
    assert signal is not None


def _scanner_observation(*, rvol: str = "1.8", spread: str = "1.8", dollar: str = "12000000",
                         move: str = "80") -> ScannerObservation:
    price = Decimal("10")
    volume = Decimal(dollar) / price
    return ScannerObservation(
        symbol="DISC", timestamp=datetime(2026, 9, 17, 14, 0, tzinfo=UTC), price=price,
        previous_close=price / (Decimal("1") + Decimal(move) / Decimal("100")),
        current_volume=volume, average_30_day_volume=volume / Decimal(rvol),
        float_shares=Decimal("1000000"), bid=price - price * Decimal(spread) / 200,
        ask=price + price * Decimal(spread) / 200, catalyst=CatalystType.EARNINGS,
        catalyst_headline=None, tradable=True, halted=False, asset_class=AssetClass.STOCK,
        catalyst_status=CatalystStatus.TRUE,
    )


def test_adaptive_discovery_retains_exceptional_low_rvol_and_spread_candidate():
    runtime = WarriorMomentumRuntime(WarriorMomentumConfig(adaptive_context_enabled=True))
    discovered = runtime.discover(_scanner_observation(), (), session="REGULAR")
    assert ReasonCode.RVOL_LOW not in discovered.reason_codes
    assert ReasonCode.SPREAD_WIDE not in discovered.reason_codes
    assert discovered.discovery_qualified is True


def test_adaptive_discovery_does_not_follow_weak_same_rvol_candidate():
    runtime = WarriorMomentumRuntime(WarriorMomentumConfig(adaptive_context_enabled=True))
    discovered = runtime.discover(
        _scanner_observation(rvol="1.8", spread="1.8", dollar="300000", move="8"),
        (), session="REGULAR",
    )
    assert discovered.discovery_qualified is False


def test_adaptive_discovery_keeps_absolute_liquidity_and_catastrophic_spread_hard():
    config = WarriorMomentumConfig(adaptive_context_enabled=True)
    low_liquidity = WarriorMomentumRuntime(config).discover(
        _scanner_observation(dollar="100000"), (), session="REGULAR")
    catastrophic = WarriorMomentumRuntime(config).discover(
        _scanner_observation(spread="6"), (), session="REGULAR")
    assert ReasonCode.LIQUIDITY_LOW in low_liquidity.reason_codes
    assert ReasonCode.SPREAD_WIDE in catastrophic.reason_codes


def test_adaptive_flag_is_explicit_and_off_by_default(monkeypatch):
    monkeypatch.delenv("ATLAS_WARRIOR_ADAPTIVE_CONTEXT_ENABLED", raising=False)
    assert WarriorMomentumConfig.from_env().adaptive_context_enabled is False
    monkeypatch.setenv("ATLAS_WARRIOR_ADAPTIVE_CONTEXT_ENABLED", "true")
    assert WarriorMomentumConfig.from_env().adaptive_context_enabled is True


def test_daily_participation_uses_intraday_velocity_not_30_day_rvol():
    context = WarriorAdaptiveContext()
    first = context.evaluate(candidate(symbol="DAILY", rvol="0.01", dollar="80000", move="180"))
    second = context.evaluate(candidate(
        symbol="DAILY", rvol="0.01", dollar="180000", move="180",
    ))
    assert first.daily_dollar_volume == Decimal("80000")
    assert second.participation_velocity > Decimal("0")
    assert second.observation_count == 2
    assert second.decision is not AdaptiveDecision.READY


def test_fast_intraday_growth_can_overcome_bootstrap_floor_without_rvol_authority():
    context = WarriorAdaptiveContext()
    first = context.evaluate(candidate(
        symbol="FAST", rvol="0.01", dollar="80000", move="180",
    ))
    rapid = replace_candidate_timestamp(candidate(
        symbol="FAST", rvol="0.01", dollar="180000", move="180",
    ), seconds=6)
    second = context.evaluate(rapid)
    assert first.decision is AdaptiveDecision.REJECT
    assert second.participation_velocity > Decimal("100000")
    assert second.decision is not AdaptiveDecision.REJECT
    assert AdaptiveReason.PARTICIPATION_IMPROVING in second.reasons


def test_premarket_bootstrap_is_phase_aware_and_not_permanent():
    context = WarriorAdaptiveContext()
    first = context.evaluate(candidate(symbol="PRE", dollar="100000", move="120"))
    assert first.bootstrap_required is False  # regular session fixture
    context = WarriorAdaptiveContext()
    pre = replace(candidate(symbol="PRE", dollar="100000", move="120"), session="PREMARKET")
    assert context.evaluate(pre).bootstrap_required is True
    later = replace_candidate_timestamp(replace(pre, dollar_volume=Decimal("180000")), seconds=6)
    assert context.evaluate(later).bootstrap_required is True


def test_daily_state_resets_on_new_trading_date_and_sessions_are_separate():
    context = WarriorAdaptiveContext()
    regular = context.evaluate(candidate(symbol="RESET", dollar="500000", move="40"))
    after_hours = replace(candidate(symbol="OTHER", dollar="500000"), session="AFTER_HOURS")
    context.evaluate(after_hours)
    assert set(context._session_metrics) >= {"REGULAR", "AFTER_HOURS"}
    next_day = replace(candidate(symbol="RESET", dollar="500000"),
                       timestamp=datetime(2026, 9, 18, 14, 0, tzinfo=UTC))
    reset = context.evaluate(next_day)
    assert reset.observation_count == 1
    assert reset.participation_velocity == Decimal("0")
    assert set(context._session_metrics) == {"REGULAR"}


def replace_candidate_timestamp(value, *, seconds: int):
    return replace(value, timestamp=value.timestamp.replace(second=value.timestamp.second + seconds))


def test_adaptive_observation_route_retains_exceptional_quality_miss():
    decision = evaluate_candidate(_scanner_observation(rvol="0.01", dollar="13000", move="368"))
    assert decision.qualified is False
    assert {"relative_volume", "dollar_volume"}.issubset(set(decision.technical_failed_rules))
    assert warrior_observation_eligible(decision) is True


def test_adaptive_observation_route_accepts_strong_non_extreme_mover():
    decision = evaluate_candidate(_scanner_observation(rvol="0.2", dollar="120000", move="40"))
    assert decision.qualified is False
    assert warrior_observation_eligible(decision) is True


def test_adaptive_observation_route_rejects_weak_or_unsafe_candidates():
    weak = evaluate_candidate(_scanner_observation(rvol="0.01", dollar="13000", move="8"))
    halted = replace(weak, halted=True)
    assert warrior_observation_eligible(weak) is False
    assert warrior_observation_eligible(halted) is False
