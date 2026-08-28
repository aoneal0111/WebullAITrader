from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from app.momentum_scanner.models import CatalystStatus, CatalystType, ScannerObservation
from app.momentum_scanner.rules import calculate_metrics
from app.strategies.warrior_momentum.models import (
    MinuteBar,
    ReasonCode,
    SetupDetection,
    SetupState,
    SetupType,
    StopModel,
)
from app.strategies.warrior_momentum.runtime import WarriorMomentumRuntime, entry_rejections
from app.strategies.warrior_momentum.setups import detect_best_setup, detect_micro_pullback


def _captured_bars() -> tuple[MinuteBar, ...]:
    values = (
        ("18:47", "2.671", "2.671", "2.512", "2.530", "132248"),
        ("18:48", "2.540", "2.550", "2.510", "2.550", "54319"),
        ("18:49", "2.540", "2.545", "2.510", "2.530", "50806"),
        ("18:50", "2.525", "2.539", "2.519", "2.539", "16349"),
        ("18:51", "2.540", "2.560", "2.532", "2.560", "36510"),
        ("18:52", "2.565", "2.590", "2.551", "2.569", "28644"),
    )
    return tuple(
        MinuteBar(
            "AEMD",
            datetime.fromisoformat(f"2026-08-28T{minute}:00+00:00"),
            *(Decimal(value) for value in fields),
        )
        for minute, *fields in values
    )


def _observation() -> ScannerObservation:
    return ScannerObservation(
        symbol="AEMD",
        timestamp=datetime(2026, 8, 28, 18, 53, 0, 779000, tzinfo=UTC),
        price=Decimal("2.545"),
        previous_close=Decimal("2.17"),
        current_volume=Decimal("43405307"),
        average_30_day_volume=Decimal("600490.3533333333333333333333"),
        float_shares=Decimal("711136"),
        bid=Decimal("2.540"),
        ask=Decimal("2.550"),
        catalyst=CatalystType.NONE,
        catalyst_headline=None,
        tradable=True,
        halted=False,
        catalyst_status=CatalystStatus.FALSE,
    )


def test_aemd_captured_sequence_reproduces_no_setup_without_price_rounding() -> None:
    observation = _observation()
    runtime = WarriorMomentumRuntime()

    metrics = calculate_metrics(observation)
    candidate = runtime.discover(observation, _captured_bars(), session="REGULAR")

    assert observation.price == Decimal("2.545")
    assert metrics.dollar_volume == Decimal("2.545") * observation.current_volume
    assert candidate.price == Decimal("2.545")
    assert detect_micro_pullback(_captured_bars()).state is SetupState.NOT_FORMED
    assert detect_best_setup(_captured_bars()) is None
    assert entry_rejections(candidate, runtime.config) == (ReasonCode.NO_SETUP,)
    assert runtime.entry_signal(candidate) is None


def test_forming_remains_non_executable_and_triggered_is_required() -> None:
    runtime = WarriorMomentumRuntime()
    candidate = runtime.discover(_observation(), _captured_bars(), session="REGULAR")
    forming = replace(
        candidate,
        setup=SetupDetection(
            SetupType.MICRO_PULLBACK,
            SetupState.FORMING,
            Decimal("70"),
            Decimal("2.56128"),
            Decimal("2.519"),
            StopModel.MICRO_PULLBACK_LOW,
        ),
    )

    assert entry_rejections(forming, runtime.config) == (ReasonCode.NO_SETUP,)
    assert runtime.entry_signal(forming) is None

    triggered = replace(
        forming,
        setup=replace(forming.setup, state=SetupState.TRIGGERED),
    )
    signal = runtime.entry_signal(triggered)
    assert signal is not None
    assert signal.entry_trigger == Decimal("2.56128")
    assert signal.stop_price == Decimal("2.519")
