import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QWidget

from app.gui.models import (
    AtlasActivityRow,
    AtlasActivitySnapshot,
    AIThinkingSnapshot,
    HealthDashboardSnapshot,
    OrdersSnapshot,
    PaperValidationDashboardSnapshot,
    PortfolioDashboardSnapshot,
    PositionsSnapshot,
    RuntimeSnapshot,
    WatchlistRow,
    WatchlistSnapshot,
)
from app.gui.pages.dashboard import DashboardPage
from app.gui.design.theme import application_stylesheet
from app.gui.design.tokens import Colors
from app.gui.widgets.infrastructure_strip import InfrastructureStrip
from app.gui.widgets.ai_thinking_panel import AIThinkingPanel
from app.gui.widgets.market_workspace import MarketWorkspace
from app.gui.widgets.orders_panel import OrdersPanel
from app.gui.widgets.paper_validation_panel import PaperValidationPanel
from app.gui.widgets.portfolio_summary_strip import PortfolioSummaryStrip
from app.gui.widgets.positions_panel import PositionsPanel
from app.gui.widgets.runtime_control_header import RuntimeControlHeader
from app.gui.widgets.global_status_bar import GlobalStatusBar


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("mode", ("TEST", "PAPER", "LIVE", "PRODUCTION"))
def test_runtime_header_displays_truthful_mode(application, mode) -> None:
    del application
    header = RuntimeControlHeader()
    header.render(replace(RuntimeSnapshot.initial(), environment=mode))

    assert header._metrics["Mode"].text() == mode
    assert header._metrics["Mode"].property("status") in {
        "good", "warn", "danger"
    }


@pytest.mark.parametrize(
    ("status", "tone"),
    (("CONNECTED", "good"), ("DEGRADED", "warn"), ("DISABLED", "neutral")),
)
def test_infrastructure_state_palette(application, status, tone) -> None:
    del application
    strip = InfrastructureStrip()
    strip.render(
        HealthDashboardSnapshot(
            overall_status=status,
            status_level=tone,
            metrics=(("Market Data", status),),
            incident="No incidents.",
        )
    )

    card = strip._cards["Market Data"]
    assert status in card._indicator.text()
    assert card._indicator.property("status") == tone


@pytest.mark.parametrize(
    ("value", "tone"),
    (("+$12.50", "good"), ("-$7.25", "danger")),
)
def test_portfolio_pnl_uses_directional_color(application, value, tone) -> None:
    del application
    strip = PortfolioSummaryStrip()
    strip.render(
        PortfolioDashboardSnapshot(
            metrics=(("Unrealized P/L", value),),
            highlights=(),
        )
    )

    assert strip._cards["Unrealized P/L"]._value.property("tone") == tone


def test_compact_atlas_focus_and_activity_use_projection_snapshots(application) -> None:
    del application
    workspace = MarketWorkspace()
    snapshot = WatchlistSnapshot(
        rows=(
            WatchlistRow(
                symbol="XYZ", selected=True, latest_price="500.00",
                change="+2.00", change_percent="+0.40%", bid="499.99",
                ask="500.01", volume="100", market_status="OPEN",
                last_update="10:00:00", stale="LIVE", rank="1",
            ),
        )
    )
    workspace.render(snapshot)
    workspace.render_activity(AtlasActivitySnapshot(rows=(
        AtlasActivityRow("Universe", "7300", "good"),
        AtlasActivityRow("Evaluating", "Unknown"),
    )))

    assert workspace.watchlist._table.columnCount() == 9
    assert workspace.watchlist._table.item(0, 1).text().endswith("XYZ")
    assert workspace.watchlist._table.item(0, 3).text() == "+0.40%"
    assert workspace.atlas_activity._rows["Universe"].text() == "●  7300"
    assert workspace.atlas_activity._rows["Evaluating"].text() == (
        "●  Unknown"
    )


def test_atlas_focus_exposes_rich_projection_and_selection_without_fabrication(application) -> None:
    del application
    workspace = MarketWorkspace()
    selected = []
    operator_selected = []
    atlas_selected = []
    workspace.chart_symbol_selected.connect(selected.append)
    workspace.operator_symbol_selected.connect(operator_selected.append)
    workspace.atlas_symbol_selected.connect(atlas_selected.append)
    workspace.render(WatchlistSnapshot(
        rows=(WatchlistRow(
            symbol="XYZ", selected=False, latest_price="--", change="--",
            change_percent="--", bid="--", ask="--", volume="--",
            market_status="--", last_update="--", stale="--",
            rank="1", score="87.5", relative_volume="2.40x",
            catalyst="EARNINGS: Reported results", freshness="LIVE",
            session="REGULAR",
        ),),
        scanner_status="RUNNING",
        candidate_count=1,
    ))

    assert workspace.watchlist._scanner_status.text() == "Atlas Scanner: Running"
    assert workspace.watchlist._candidate_count.text() == "Candidates: 1"
    assert workspace.watchlist._table.item(0, 2).text() == "--"
    assert workspace.trade_intelligence._watching_values["Catalyst"].text() == (
        "EARNINGS: Reported results"
    )
    workspace.watchlist._select_row(0, 6)
    assert selected == []
    assert operator_selected == []
    assert atlas_selected == []
    assert workspace.watchlist._table.rowCount() == 1


def test_atlas_scanner_filters_candidate_classifications(
    application,
) -> None:
    del application
    workspace = MarketWorkspace()
    workspace.render(
        WatchlistSnapshot(
            rows=(
                WatchlistRow(
                    symbol="QUAL",
                    selected=False,
                    latest_price="10.00",
                    change="--",
                    change_percent="+20.00%",
                    bid="--",
                    ask="--",
                    volume="100,000",
                    market_status="REGULAR",
                    last_update="10:00:00",
                    stale="LIVE",
                    rank="1",
                    classification="QUALIFYING",
                ),
                WatchlistRow(
                    symbol="WATCH",
                    selected=False,
                    latest_price="5.00",
                    change="--",
                    change_percent="+12.00%",
                    bid="--",
                    ask="--",
                    volume="80,000",
                    market_status="REGULAR",
                    last_update="10:00:01",
                    stale="LIVE",
                    rank="2",
                    classification="WATCHING",
                ),
                WatchlistRow(
                    symbol="CLOSE",
                    selected=False,
                    latest_price="4.50",
                    change="--",
                    change_percent="+9.50%",
                    bid="--",
                    ask="--",
                    volume="75,000",
                    market_status="REGULAR",
                    last_update="10:00:02",
                    stale="LIVE",
                    rank="3",
                    classification="NEAR MISS",
                ),
            ),
            scanner_status="RUNNING",
            candidate_count=3,
        )
    )

    assert workspace.watchlist._view_buttons["All"].isEnabled()
    assert workspace.watchlist._view_buttons["Qualifying"].isEnabled()
    assert workspace.watchlist._view_buttons["Watching"].isEnabled()
    assert workspace.watchlist._view_buttons["Near Miss"].isEnabled()
    assert workspace.watchlist._table.rowCount() == 3

    workspace.watchlist._view_buttons["Qualifying"].click()

    assert workspace.watchlist._table.rowCount() == 1
    assert workspace.watchlist._table.item(0, 1).text().endswith("QUAL")

    workspace.watchlist._view_buttons["Watching"].click()

    assert workspace.watchlist._table.rowCount() == 1
    assert workspace.watchlist._table.item(0, 1).text().endswith("WATCH")

    workspace.watchlist._view_buttons["Near Miss"].click()

    assert workspace.watchlist._table.rowCount() == 1
    assert workspace.watchlist._table.item(0, 1).text().endswith("CLOSE")

    workspace.watchlist._view_buttons["All"].click()

    assert workspace.watchlist._table.rowCount() == 3


def test_running_scanner_with_zero_candidates_stays_truthful(application) -> None:
    del application
    workspace = MarketWorkspace()
    workspace.render(WatchlistSnapshot(scanner_status="RUNNING", candidate_count=0))
    assert workspace.watchlist._scanner_status.text() == "Atlas Scanner: Running"
    assert workspace.watchlist._candidate_count.text() == "Candidates: 0"
    assert workspace.watchlist._table.rowCount() == 0
    assert "Atlas is scanning" in workspace.watchlist._table._empty_state.text()


def test_trade_intelligence_preserves_honest_empty_state_without_candidate(
    application,
) -> None:
    del application
    workspace = MarketWorkspace()
    workspace.render(WatchlistSnapshot(rows=(WatchlistRow(
        symbol="XYZ", selected=False, latest_price="500.00",
        change="+2.00", change_percent="+0.40%", bid="499.99",
        ask="500.01", volume="100", market_status="OPEN",
        last_update="10:00:00", stale="LIVE",
    ),)))

    assert workspace.chart_view is None
    assert workspace.trade_intelligence._symbol.text() == "--"
    assert "Select an opportunity" in workspace.trade_intelligence._reason.text()


def test_ai_thinking_panel_displays_only_projected_decision_facts(
    application,
) -> None:
    del application
    panel = AIThinkingPanel()
    panel.render(AIThinkingSnapshot(
        objective="Searching for Opportunities",
        operational_state="Evaluating high-confidence candidates.",
        reasoning="Projected decision explanation.",
        last_decision="BUY XYZ",
        confidence="91%",
        next_evaluation="Unknown",
        tone="good",
    ))

    assert panel.objective.text() == "Searching for Opportunities"
    assert panel.reasoning.text() == "Projected decision explanation."
    assert panel.last_decision.text() == "BUY XYZ"
    assert panel.confidence.text() == "91%"
    assert panel.next_evaluation.text() == "Unknown"


def test_operator_tables_expose_reference_columns_and_real_rows(application) -> None:
    del application
    positions = PositionsPanel()
    positions.render(PositionsSnapshot(rows=((
        "XYZ", "LONG", "2", "$10.00", "$11.00", "+$2.00", "+10.00%",
        "$0.00", "10:00:00",
    ),)))
    orders = OrdersPanel()
    orders.render(OrdersSnapshot(rows=((
        "XYZ", "BUY", "LIMIT", "2", "0", "2", "$10.00",
        "Not available", "Not available", "WORKING",
    ),)))

    assert positions._table.columnCount() == 9
    assert positions._table.item(0, 0).text() == "XYZ"
    assert orders._table.columnCount() == 10
    assert orders._table.item(0, 9).text() == "WORKING"


def test_health_diagnostics_and_paper_validation_remain_visible(application) -> None:
    del application
    dashboard = DashboardPage()
    diagnostics = HealthDashboardSnapshot(
        overall_status="DEGRADED",
        status_level="warn",
        metrics=(
            ("Trading Environment", "PAPER"),
            ("Market Data Environment", "PRODUCTION"),
            ("Trading REST", "CONNECTED"),
            ("Streaming", "DEGRADED"),
            ("Subscription", "ACCEPTED"),
            ("Entitlement", "GRANTED"),
            ("Market Data Probe", "SUPPORTED"),
            ("Scanner", "READY"),
        ),
        incident="Stream reconnecting.",
    )
    dashboard.operator_health_panel.render(diagnostics)
    labels = {label.text() for label in dashboard.operator_health_panel.findChildren(QLabel)}
    assert {name for name, _ in diagnostics.metrics}.issubset(labels)

    panel: PaperValidationPanel = dashboard.paper_validation_panel
    panel.render(PaperValidationDashboardSnapshot(
        account="PASS", orders="PASS", buying_power="PASS",
        positions="PASS", reconciliation="PASS", overall="PASS",
        message="Validation completed.",
    ))
    assert panel.overall_badge.text() == "OVERALL: PASS"
    assert panel.status_badges["Orders"].text() == "PASS"


def test_health_panel_and_item_views_use_atlas_theme(application) -> None:
    del application
    dashboard = DashboardPage()
    health = dashboard.operator_health_panel

    assert health.objectName() == "healthPanel"
    assert health.findChild(QWidget, "healthMetrics") is not None
    assert health.findChild(QWidget, "healthScrollViewport") is not None

    stylesheet = application_stylesheet()
    for selector in (
        "QAbstractItemView", "QTreeView", "QTableView", "QTableWidget",
        "QWidget#healthPanel", "QWidget#healthMetrics",
        "QWidget#healthScrollViewport",
    ):
        assert selector in stylesheet
    assert f"background: {Colors.SURFACE};" in stylesheet
    assert f"selection-background-color: {Colors.ACCENT_SOFT};" in stylesheet


def test_dashboard_and_market_workspace_switch_responsive_orientation(application) -> None:
    page = DashboardPage()
    page.resize(1600, 900)
    page.show()
    application.processEvents()
    assert page.market_workspace.layout_mode == "wide"

    page.resize(1000, 720)
    application.processEvents()

    assert page.market_workspace.layout_mode in {"compact", "wide"}

    market = MarketWorkspace()
    market.resize(1000, 500)
    market.show()
    application.processEvents()
    assert market.splitter.orientation() == Qt.Orientation.Horizontal
    assert market.splitter.indexOf(market.intelligence_rail) == -1
    assert market.splitter.count() == 2

    market.resize(700, 700)
    application.processEvents()
    assert market.splitter.orientation() == Qt.Orientation.Horizontal
    assert market.splitter.indexOf(market.intelligence_rail) == -1


@pytest.mark.parametrize(
    ("width", "height"),
    ((1280, 720), (1366, 768), (1920, 1080), (2560, 1440)),
)
def test_commercial_dashboard_preserves_panels_at_target_viewports(
    application, width, height,
) -> None:
    page = DashboardPage()
    page.market_workspace.render_activity(AtlasActivitySnapshot(rows=tuple(
        AtlasActivityRow(label, "Unknown")
        for label in (
            "Universe", "Evaluating", "Candidates", "Open Positions",
            "Pending Orders", "Market Data", "Broker",
        )
    )))
    page.resize(width, height)
    page.show()
    application.processEvents()

    assert len(page.findChildren(QScrollArea)) == 1
    assert all(
        area.objectName() == "sectionScrollArea"
        for area in page.findChildren(QScrollArea)
    )
    assert page.market_workspace.splitter.orientation() == Qt.Orientation.Horizontal
    assert page.market_workspace.height() >= 420

    ai = page.market_workspace.ai_thinking_section
    focus = page.market_workspace.focus_section
    reasoning = page.market_workspace.reasoning_section

    assert not ai.isVisible()
    assert reasoning.isVisible()
    assert focus.isVisible()

    assert page.market_workspace.splitter.widget(0) is page.market_workspace.left_column
    assert page.market_workspace.splitter.widget(1) is page.market_workspace.right_workspace
    assert page.market_workspace.trade_intelligence.isVisible()
    assert page.market_workspace.chart_view is None


def test_production_gui_contains_no_reference_sample_values() -> None:
    forbidden = ("125,430.50", "1,247.35", "AAPL 229.96", "41.3%")
    root = os.path.join(os.path.dirname(__file__), "..", "..", "app", "gui")
    for directory, _, files in os.walk(root):
        for filename in files:
            if not filename.endswith(".py"):
                continue
            with open(os.path.join(directory, filename), encoding="utf-8") as source:
                contents = source.read()
            assert not any(value in contents for value in forbidden)


def test_dashboard_uses_atlas_operator_terminology(application) -> None:
    del application
    dashboard = DashboardPage()
    labels = {
        label.text()
        for root in (dashboard, dashboard.market_workspace.intelligence_rail)
        for label in root.findChildren(QLabel)
    }

    assert "OPPORTUNITIES" in labels
    assert "ATLAS TRADE INTELLIGENCE" in labels
    assert "Atlas Activity" in labels
    assert "Mission Status" in labels
    assert "Infrastructure" in labels
    assert "ATLAS X" in labels
    assert "Watchlist" not in labels
    assert "Market Summary" not in labels
    assert "AI Thinking" in labels
    assert dashboard.operator_workspace.tabs.tabText(3) == "Mission Timeline"
    assert "PORTFOLIO SUMMARY" not in labels
    assert dashboard.operator_workspace.tabs.tabText(5) == "System Health"


def test_status_bar_summarizes_capabilities(application) -> None:
    del application
    status = GlobalStatusBar(version="test")
    status.render_health(HealthDashboardSnapshot(
        overall_status="HEALTHY",
        status_level="good",
        metrics=(),
        incident="No incidents.",
        capabilities=(
            ("Stocks", "Available"),
            ("Options", "Unavailable (Broker Not Supported)"),
            ("Crypto", "Unknown"),
        ),
        sessions=(("Overnight", "Unavailable (Subscription Required)"),),
    ))

    assert "Stocks \u2713" in status.capabilities.text()
    assert "Options \u2717" in status.capabilities.text()
    assert "Crypto ?" in status.capabilities.text()
    assert "Overnight \u2717" in status.capabilities.text()


def test_market_workspace_puts_trade_intelligence_in_primary_splitter(application) -> None:
    del application
    workspace = MarketWorkspace()

    assert workspace.splitter.orientation() == Qt.Orientation.Horizontal
    assert workspace.top_splitter.orientation() == Qt.Orientation.Horizontal
    assert workspace.splitter.widget(0) is workspace.left_column
    assert workspace.splitter.widget(1) is workspace.right_workspace
    assert workspace.ai_thinking_section.parent() is not None
    assert workspace.activity_section.parent() is not None
    assert workspace.top_splitter.count() == 2
    assert workspace.splitter.indexOf(workspace.intelligence_rail) == -1
