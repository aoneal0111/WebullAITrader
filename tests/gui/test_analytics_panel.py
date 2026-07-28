from datetime import datetime, timezone
from decimal import Decimal

from PySide6.QtWidgets import QAbstractItemView

from app.analytics import (
    AnalyticsSnapshot,
    AnalyticsStatus,
    PerformanceMetrics,
    RiskMetrics,
    StrategyMetrics,
    SymbolMetrics,
    TimeMetrics,
)
from app.gui.widgets.analytics_panel import AnalyticsPanel


NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


def test_analytics_panel_renders_all_read_only_sections(qtbot) -> None:
    performance = PerformanceMetrics(
        total_trades=1,
        winning_trades=1,
        win_rate=Decimal("1"),
        average_gain=Decimal("42"),
        largest_winner=Decimal("42"),
        net_realized_pnl=Decimal("42"),
        gross_profit=Decimal("42"),
    )
    snapshot = AnalyticsSnapshot(
        AnalyticsStatus.READY,
        performance,
        RiskMetrics(peak_equity=Decimal("1042")),
        StrategyMetrics(
            by_strategy_version=(("v1", 1, Decimal("42")),),
        ),
        (SymbolMetrics("AAPL", performance),),
        (TimeMetrics("DAY", "2026-07-28", 1, 1, Decimal("42")),),
        None,
        None,
        NOW,
    )
    panel = AnalyticsPanel()
    qtbot.addWidget(panel)
    panel.render(snapshot)

    assert panel.tabs.count() == 6
    assert panel.overview_values["Total Trades"].text() == "1"
    assert panel.symbols.item(0, 0).text() == "AAPL"
    assert panel.strategies.item(0, 1).text() == "v1"
    assert panel.time_analysis.item(0, 1).text() == "2026-07-28"
    assert (
        panel.performance.editTriggers()
        == QAbstractItemView.EditTrigger.NoEditTriggers
    )
