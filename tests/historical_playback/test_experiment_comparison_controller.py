from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.analytics import (
    AnalyticsSnapshot,
    AnalyticsStatus,
    PerformanceMetrics,
    RiskMetrics,
    StrategyMetrics,
)
from app.backtesting.comparison import ComparisonEngine
from app.backtesting.market_feed import InMemoryHistoricalMarketFeed
from app.backtesting.models import (
    BacktestConfiguration,
    Experiment,
    ExperimentResult,
    PlaybackStatus,
)
from app.backtesting.repository import ExperimentRepository
from app.composition import create_desktop_composition
from app.composition.desktop_runtime_config import DesktopRuntimeConfiguration

NOW = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)


def result(
    identifier: str,
    *,
    pnl: str,
    profit_factor: str | None,
    recovery: str | None,
    duration: timedelta | None,
) -> ExperimentResult:
    performance = PerformanceMetrics(
        total_trades=2,
        winning_trades=1,
        losing_trades=1,
        win_rate=Decimal("0.5"),
        average_gain=Decimal("100"),
        average_loss=Decimal("-40"),
        profit_factor=(
            None if profit_factor is None else Decimal(profit_factor)
        ),
        expectancy=Decimal(pnl) / 2,
        average_holding_duration=duration,
        average_trade_duration=duration,
        largest_winner=Decimal("100"),
        largest_loser=Decimal("-40"),
        net_realized_pnl=Decimal(pnl),
        gross_profit=Decimal("100"),
        gross_loss=Decimal("-40"),
    )
    analytics = AnalyticsSnapshot(
        AnalyticsStatus.READY,
        performance,
        RiskMetrics(
            maximum_drawdown=Decimal("20"),
            recovery_factor=(
                None if recovery is None else Decimal(recovery)
            ),
        ),
        StrategyMetrics(),
        (),
        (),
        None,
        None,
        NOW,
    )
    return ExperimentResult(
        Experiment(identifier, identifier.title(), BacktestConfiguration("v1")),
        PlaybackStatus.COMPLETED,
        NOW,
        NOW + timedelta(hours=1),
        3,
        f"session-{identifier}",
        analytics,
        NOW + timedelta(hours=1),
    )


def test_repository_order_duplicates_and_comparison_deltas() -> None:
    repository = ExperimentRepository()
    candidate = result(
        "candidate", pnl="80", profit_factor="3", recovery="4",
        duration=timedelta(minutes=20),
    )
    baseline = result(
        "baseline", pnl="60", profit_factor="2.5", recovery="3",
        duration=timedelta(minutes=30),
    )
    repository.save(candidate)
    repository.save(baseline)
    assert tuple(
        item.experiment.experiment_id for item in repository.list()
    ) == ("baseline", "candidate")
    assert repository.get("baseline") is baseline
    with pytest.raises(ValueError, match="duplicate"):
        repository.save(baseline)

    comparison = ComparisonEngine().compare(baseline, candidate)
    metrics = {item.name: item for item in comparison.metrics}
    assert metrics["net_realized_pnl"].delta == Decimal("20")
    assert metrics["profit_factor"].delta == Decimal("0.5")
    assert metrics["recovery_factor"].delta == Decimal("1")
    assert metrics["average_holding_duration"].delta == timedelta(minutes=-10)


def test_comparison_preserves_nullable_metrics() -> None:
    baseline = result(
        "baseline", pnl="0", profit_factor=None, recovery=None,
        duration=None,
    )
    candidate = result(
        "candidate", pnl="0", profit_factor="2", recovery="1",
        duration=timedelta(minutes=5),
    )
    metrics = {
        item.name: item
        for item in ComparisonEngine().compare(baseline, candidate).metrics
    }
    assert metrics["profit_factor"].delta is None
    assert metrics["recovery_factor"].delta is None
    assert metrics["average_holding_duration"].delta is None


def test_controller_orchestrates_subscriptions_selection_comparison_and_close(
    tmp_path: Path,
    historical_events,
) -> None:
    composition = create_desktop_composition(
        configuration=DesktopRuntimeConfiguration(
            recording_directory=tmp_path,
        )
    )
    observed = []
    identifier = composition.backtesting_controller.subscribe(observed.append)
    try:
        composition.backtesting_controller.load(
            InMemoryHistoricalMarketFeed(historical_events)
        )
        for experiment_id in ("one", "two"):
            composition.backtesting_controller.start_experiment(
                Experiment(
                    experiment_id,
                    experiment_id.title(),
                    BacktestConfiguration("v1"),
                )
            )
        snapshot = composition.backtesting_controller.compare("one", "two")
        assert snapshot.comparison.baseline_experiment_id == "one"
        assert len(snapshot.experiments) == 2
        assert len(observed) == 5
        assert composition.backtesting_controller.unsubscribe(identifier)
    finally:
        composition.close(timeout_seconds=1.0)
        composition.close(timeout_seconds=1.0)
    assert composition.backtesting_controller.snapshot().playback.status is (
        PlaybackStatus.CLOSED
    )
