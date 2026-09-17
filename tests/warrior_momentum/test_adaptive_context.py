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
)


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
