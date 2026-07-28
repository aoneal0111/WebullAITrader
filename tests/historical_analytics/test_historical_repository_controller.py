from datetime import datetime, timezone
from decimal import Decimal

from app.analytics import (
    AnalyticsController,
    AnalyticsEngine,
    AnalyticsRepository,
    AnalyticsStatus,
)


NOW = datetime(2026, 7, 28, 20, 0, tzinfo=timezone.utc)


def test_repository_consumes_snapshot_and_filters_symbol_and_strategy(
    analytics_event_store_snapshot,
) -> None:
    repository = AnalyticsRepository()
    dataset = repository.load(analytics_event_store_snapshot)
    assert tuple(trade.symbol for trade in dataset.trades) == (
        "AAPL",
        "MSFT",
    )
    assert dataset.trades[0].committee_outcome == "APPROVED"
    assert repository.load(
        analytics_event_store_snapshot,
        symbol="AAPL",
        strategy_version="v2",
    ).trades[0].realized_pnl == Decimal("100")


def test_controller_refresh_filter_aggregate_notifications_and_cleanup(
    analytics_event_store_snapshot,
) -> None:
    controller = AnalyticsController(
        AnalyticsRepository(),
        AnalyticsEngine(),
        lambda: analytics_event_store_snapshot,
        clock=lambda: NOW,
    )
    observed = []
    listener = controller.subscribe(observed.append)
    assert controller.snapshot().status is AnalyticsStatus.READY
    assert controller.snapshot().updated_at == NOW
    assert controller.filter(symbol="AAPL").performance.total_trades == 1
    assert controller.snapshot().selected_symbol == "AAPL"
    assert controller.aggregate().performance.total_trades == 2
    assert len(observed) == 3
    assert controller.unsubscribe(listener)
    controller.close()
    controller.close()
    assert controller.snapshot().status is AnalyticsStatus.CLOSED
