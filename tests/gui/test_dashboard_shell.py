import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QScrollArea

from app.composition import create_desktop_composition
from app.gui.main_window import MainWindow
from app.gui.models import (
    HealthDashboardSnapshot,
    WatchlistRow,
    WatchlistSnapshot,
)
from app.gui.shell.sidebar import Sidebar
from app.gui.widgets.infrastructure_strip import InfrastructureStrip
from app.gui.widgets.market_workspace import MarketWorkspace
from app.gui.widgets.operator_workspace import OperatorWorkspace
from app.operations_core import ApplicationState
from app.read_models.health import HealthState
from app.read_models.portfolio import PortfolioSummary
from app.read_models.watchlist import WatchlistEntry, WatchlistState


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(application):
    composition = create_desktop_composition()
    desktop = MainWindow(
        composition.bus,
        composition.state_store,
        composition.runtime_service,
        composition.trading_service,
        composition.order_command_factory,
    )
    yield desktop
    desktop.close()
    composition.close()


def test_navigation_rail_uses_reference_routes_without_reindexing(
    application,
) -> None:
    del application
    sidebar = Sidebar()
    requested = []
    sidebar.page_requested.connect(requested.append)

    sidebar.buttons["Replay"].click()
    sidebar.buttons["Event Store"].click()

    assert sidebar.ITEMS == (
        "Dashboard",
        "Replay",
        "Event Store",
        "Analytics",
        "Experiments",
        "Settings",
    )
    assert requested == [8, 5]


def test_shell_retains_existing_pages_and_command_boundaries(window) -> None:
    assert window.pages.count() == 9
    assert window.pages.widget(0) is window.dashboard
    assert window.pages.widget(8) is window.replay
    assert window.start_button is (
        window.dashboard.runtime_header.resume_button
    )
    assert window.stop_button is window.dashboard.runtime_header.stop_button
    assert window.flatten_button.isEnabled() is False


def test_supported_minimum_size_has_no_horizontal_dashboard_scroll(
    application,
    window,
) -> None:
    window.resize(1180, 760)
    window.show()
    application.processEvents()

    scroll = window.dashboard.findChild(QScrollArea)
    assert scroll.horizontalScrollBar().maximum() == 0
    assert scroll.verticalScrollBar().maximum() == 0
    for button in (
        window.start_button,
        window.pause_button,
        window.stop_button,
        window.flatten_button,
    ):
        assert button.width() >= button.minimumSizeHint().width()


@pytest.mark.parametrize(
    ("width", "height"),
    ((1180, 760), (1440, 900), (1920, 1080)),
)
def test_dashboard_fits_supported_resolutions_without_clipping(
    application,
    window,
    width,
    height,
) -> None:
    window.resize(width, height)
    window.show()
    application.processEvents()

    scroll = window.dashboard.findChild(QScrollArea)
    assert scroll.horizontalScrollBar().maximum() == 0
    assert scroll.verticalScrollBar().maximum() == 0
    assert window.dashboard.operator_workspace.height() > 150
    assert window.dashboard.market_workspace.height() > (
        window.dashboard.operator_workspace.height()
    )


def test_portfolio_summary_exposes_visual_metric_hierarchy(window) -> None:
    cards = window.dashboard.portfolio_summary._cards

    assert cards["Equity"].property("emphasis") == "primary"
    assert cards["Total P/L"].property("emphasis") == "primary"
    assert cards["Gross Exposure"].property("emphasis") == "medium"
    assert cards["Buying Power"].property("emphasis") == "medium"
    assert cards["Open Positions"]._value.objectName() == (
        "compactMetricValue"
    )


def test_operator_workspace_exposes_projection_backed_tabs(
    application,
) -> None:
    del application
    workspace = OperatorWorkspace()

    assert tuple(
        workspace.tabs.tabText(index)
        for index in range(workspace.tabs.count())
    ) == (
        "Positions",
        "Orders",
        "Decisions",
        "Timeline",
        "Lifecycle",
        "Health",
    )


def test_infrastructure_unknowns_are_not_rendered_as_healthy(
    application,
) -> None:
    del application
    strip = InfrastructureStrip()

    strip.render(HealthDashboardSnapshot.initial())

    for card in strip._cards.values():
        assert "UNKNOWN" in card._indicator.text()
        assert card._indicator.property("status") == "neutral"


def test_unknown_system_health_uses_neutral_palette(window) -> None:
    health = window.dashboard.runtime_header._metrics["System Health"]

    assert health.text() == "UNKNOWN"
    assert health.property("status") == "neutral"


def test_market_workspace_uses_selected_watchlist_symbol_without_candles(
    application,
) -> None:
    del application
    workspace = MarketWorkspace()
    workspace.render(
        WatchlistSnapshot(
            rows=(
                WatchlistRow(
                    symbol="AAPL",
                    selected=True,
                    latest_price="101.00",
                    change="+1.00",
                    change_percent="+1.00%",
                    bid="100.90",
                    ask="101.10",
                    volume="1,000",
                    market_status="OPEN",
                    last_update="10:00:00",
                    stale="LIVE",
                ),
            )
        )
    )

    assert workspace.chart_view._symbol.text() == "AAPL"
    assert "not configured" in workspace.chart_view._canvas._message
    assert workspace.watchlist._table.item(0, 0).text() == "\u25cf AAPL"
    assert workspace.watchlist._table.currentRow() == 0
    assert (
        workspace.watchlist._table.item(0, 3).textAlignment()
        & int(Qt.AlignmentFlag.AlignRight)
    )


def test_global_status_bar_includes_application_version(window) -> None:
    assert window.global_status.version.text().startswith("Atlas v")
    assert "Unknown" in window.global_status.data_feed.text()


def test_existing_projection_snapshots_populate_dashboard_surfaces(
    window,
) -> None:
    window._render_state(
        ApplicationState(
            health_projection=HealthState(
                runtime_status="RUNNING",
                broker_status="CONNECTED",
                market_data_status="CONNECTED",
                ai_status="READY",
                healthy=True,
            ),
            portfolio_projection=PortfolioSummary(
                total_market_value="1200",
                total_cost_basis="1000",
                realized_pnl="50",
                unrealized_pnl="200",
                total_pnl="250",
                gross_exposure="1200",
                long_exposure="1200",
                short_exposure="0",
                open_positions=1,
                working_orders=2,
                winning_positions=1,
                losing_positions=0,
                largest_position=None,
                largest_unrealized_gain=None,
                largest_unrealized_loss=None,
            ),
            watchlist_projection=WatchlistState(
                ordered_symbols=("AAPL",),
                entries=(
                    WatchlistEntry(
                        symbol="AAPL",
                        latest_price="101",
                        change="1",
                        change_percent="1",
                        market_status="OPEN",
                        stale=False,
                    ),
                ),
                selected_symbol="AAPL",
            ),
        )
    )

    broker_card = window.dashboard.infrastructure._cards["Broker"]
    assert "CONNECTED" in broker_card._indicator.text()
    assert (
        window.dashboard.portfolio_summary._cards["Total P/L"]
        ._value.text()
        == "+$250.00"
    )
    assert window.dashboard.market_workspace.chart_view._symbol.text() == "AAPL"
    assert window.global_status.broker.text() == "\u25cf  Broker Connected"
