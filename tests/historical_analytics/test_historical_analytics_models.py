from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.analytics import (
    AnalyticsSnapshot,
    AnalyticsStatus,
    PerformanceMetrics,
    RiskMetrics,
    StrategyMetrics,
    SymbolMetrics,
    TimeMetrics,
)


NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


def test_historical_analytics_models_are_frozen_and_slotted() -> None:
    snapshot = AnalyticsSnapshot.initial()
    assert snapshot.status is AnalyticsStatus.EMPTY
    assert snapshot.performance.total_trades == 0
    assert not hasattr(snapshot, "__dict__")
    with pytest.raises(FrozenInstanceError):
        snapshot.status = AnalyticsStatus.READY


@pytest.mark.parametrize(
    "factory",
    (
        lambda: PerformanceMetrics(total_trades=-1),
        lambda: PerformanceMetrics(win_rate=Decimal("1.1")),
        lambda: RiskMetrics(rolling_drawdown=[]),
        lambda: StrategyMetrics(by_decision=(("BUY", -1, Decimal("0")),)),
        lambda: SymbolMetrics("aapl", PerformanceMetrics()),
        lambda: TimeMetrics("HOUR", "", 0, 0, Decimal("0")),
        lambda: AnalyticsSnapshot(
            AnalyticsStatus.READY,
            PerformanceMetrics(),
            RiskMetrics(),
            StrategyMetrics(),
            (),
            (),
            None,
            None,
            datetime(2026, 7, 28),
        ),
    ),
)
def test_historical_analytics_model_validation(factory) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()
