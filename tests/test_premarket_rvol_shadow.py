from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.momentum_scanner import (
    AssetClass,
    CatalystStatus,
    CatalystType,
    PremarketRvolShadow,
    PremarketRvolShadowStatus,
    ScannerObservation,
)
from app.momentum_scanner.rules import evaluate_candidate
from app.strategies.warrior_momentum import WarriorMomentumRuntime


D = Decimal
AS_OF = datetime(2026, 9, 25, 8, 49, 30, tzinfo=UTC)  # 04:49:30 ET


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    volume: Decimal


def history(*, days: int = 10, include_future: bool = False) -> tuple[Bar, ...]:
    rows: list[Bar] = []
    for offset in range(1, days + 1):
        day = AS_OF - timedelta(days=offset)
        rows.extend((
            Bar(day.replace(hour=8, minute=47, second=0, microsecond=0), D("100")),
            Bar(day.replace(hour=8, minute=48, second=0, microsecond=0), D("200")),
            # This later premarket minute must not enter the 04:48 comparison.
            Bar(day.replace(hour=8, minute=49, second=0, microsecond=0), D("1000")),
            # Regular session and postmarket must never enter the curve.
            Bar(day.replace(hour=14, minute=0, second=0, microsecond=0), D("900000")),
            Bar(day.replace(hour=21, minute=0, second=0, microsecond=0), D("900000")),
        ))
    if include_future:
        rows.append(Bar(AS_OF + timedelta(days=1), D("999999999")))
    return tuple(rows)


def prepared(*, bars: tuple[Bar, ...] | None = None) -> PremarketRvolShadow:
    model = PremarketRvolShadow()
    model.prepare("XYZ", history() if bars is None else bars, as_of=AS_OF)
    return model


def test_same_time_alignment_uses_only_completed_minute_curve() -> None:
    result = prepared().lookup(
        "XYZ", timestamp=AS_OF,
        current_completed_premarket_volume=D("600"), legacy_rvol=D("0.1"),
    )

    assert result.comparison_timestamp.hour == 4
    assert result.comparison_timestamp.minute == 48
    assert result.premarket_normalized_rvol == D("2")
    assert result.historical_sample_count == 10


def test_future_sessions_and_later_minutes_cannot_leak_into_result() -> None:
    clean = prepared().lookup(
        "XYZ", timestamp=AS_OF,
        current_completed_premarket_volume=D("600"), legacy_rvol=D("0.1"),
    )
    future = prepared(bars=history(include_future=True)).lookup(
        "XYZ", timestamp=AS_OF,
        current_completed_premarket_volume=D("600"), legacy_rvol=D("0.1"),
    )

    assert future == clean


def test_regular_and_postmarket_volume_are_excluded() -> None:
    result = prepared().lookup(
        "XYZ", timestamp=AS_OF,
        current_completed_premarket_volume=D("300"), legacy_rvol=D("0.1"),
    )

    assert result.premarket_normalized_rvol == D("1")


def test_insufficient_history_is_explicitly_unavailable() -> None:
    result = prepared(bars=history(days=9)).lookup(
        "XYZ", timestamp=AS_OF,
        current_completed_premarket_volume=D("600"), legacy_rvol=D("0.1"),
    )

    assert result.status is PremarketRvolShadowStatus.UNAVAILABLE
    assert result.historical_sample_count == 9
    assert result.premarket_normalized_rvol is None
    assert result.shadow_pass is None


def test_shadow_result_is_structurally_non_authorizing() -> None:
    observation = ScannerObservation(
        symbol="XYZ", timestamp=AS_OF, price=D("5"), previous_close=D("4"),
        current_volume=D("100000"), average_30_day_volume=D("1000000"),
        float_shares=D("5000000"), bid=D("4.99"), ask=D("5.01"),
        catalyst=CatalystType.EARNINGS, catalyst_headline="earnings",
        tradable=True, halted=False, asset_class=AssetClass.STOCK,
        catalyst_status=CatalystStatus.TRUE,
    )
    before = evaluate_candidate(observation)
    runtime = WarriorMomentumRuntime()
    result = prepared().lookup(
        "XYZ", timestamp=AS_OF,
        current_completed_premarket_volume=D("900"),
        legacy_rvol=before.metrics.relative_volume,
    )
    after = evaluate_candidate(observation)

    assert before == after
    assert before.qualified is False
    assert result.shadow_pass is True
    assert result.authoritative is False
    assert not hasattr(result, "execution_authorized")
    assert runtime.authorize_live is not None


def test_telemetry_failure_cannot_change_shadow_result() -> None:
    def fail(_event) -> None:
        raise RuntimeError("diagnostic sink unavailable")

    model = PremarketRvolShadow(telemetry_sink=fail)
    model.prepare("XYZ", history(), as_of=AS_OF)

    assert model.lookup(
        "XYZ", timestamp=AS_OF,
        current_completed_premarket_volume=D("600"), legacy_rvol=D("0.1"),
    ).premarket_normalized_rvol == D("2")
