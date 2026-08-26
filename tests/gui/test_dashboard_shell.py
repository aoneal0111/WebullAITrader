import os
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QByteArray, QEvent, QSettings, Qt
from PySide6.QtWidgets import QApplication, QHeaderView, QScrollArea

from app.account_information.models import BrokerNeutralAccountInformation
from app.composition import create_desktop_composition
from app.gui.main_window import MainWindow
from app.gui.design.tokens import Dimensions
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
def window(application, tmp_path):
    composition = create_desktop_composition()
    desktop = MainWindow(
        composition.bus,
        composition.state_store,
        composition.runtime_service,
        composition.trading_service,
        composition.order_command_factory,
        settings=QSettings(
            str(tmp_path / "atlas-layout.ini"),
            QSettings.Format.IniFormat,
        ),
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
    sidebar.buttons["Activity"].click()

    assert sidebar.ITEMS == (
        "Dashboard",
        "Watchlist",
        "Positions",
        "Orders",
        "Decisions",
        "Activity",
        "Operations",
        "Replay",
        "Settings",
    )
    assert requested == [8, 5]


def test_shell_retains_existing_pages_and_command_boundaries(window) -> None:
    assert window.pages.count() == 10
    assert window.pages.widget(0) is window.dashboard
    assert window.pages.widget(8) is window.replay
    assert window.pages.widget(9) is window.operations
    assert window.operations is window.dashboard.operator_workspace
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

    scroll_areas = window.dashboard.findChildren(QScrollArea)
    assert len(scroll_areas) == 3
    assert all(
        area.objectName() == "sectionScrollArea"
        and area.horizontalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        for area in scroll_areas
    )
    for button in (
        window.start_button,
        window.pause_button,
        window.stop_button,
        window.flatten_button,
    ):
        assert button.width() >= button.minimumSizeHint().width()


@pytest.mark.parametrize(
    ("width", "height"),
    ((1280, 720), (1366, 768), (1440, 900), (1920, 1080)),
)
def test_dashboard_preserves_content_at_supported_resolutions(
    application,
    window,
    width,
    height,
) -> None:
    window.resize(width, height)
    window.show()
    application.processEvents()

    scroll_areas = window.dashboard.findChildren(QScrollArea)
    assert len(scroll_areas) == 3
    assert all(
        area.objectName() == "sectionScrollArea"
        and area.horizontalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        for area in scroll_areas
    )
    assert window.size().width() == width
    assert window.size().height() == height
    for button in (
        window.start_button,
        window.pause_button,
        window.stop_button,
        window.flatten_button,
    ):
        assert button.width() >= button.minimumSizeHint().width()
    assert window.dashboard.market_workspace.height() > 300
    market_workspace = window.dashboard.market_workspace

    assert market_workspace.splitter.orientation() == Qt.Orientation.Horizontal

    # Responsive behavior is based on the workspace's actual usable width,
    # not the outer window width. Sidebar and page margins reduce the space
    # available to MarketWorkspace.
    expected_mode = "compact" if width <= 1366 else "wide"
    assert market_workspace.layout_mode == expected_mode

    if width <= 1366:
        assert window.sidebar.compact is True
        assert window.sidebar.width() == Dimensions.NAV_COMPACT_WIDTH
        assert market_workspace.splitter.indexOf(
            market_workspace.intelligence_rail
        ) == -1
        assert window.intelligence_inspector.isHidden()
        assert all(
            button.toolTip() and button.accessibleName()
            for button in window.sidebar.buttons.values()
        )


def test_portfolio_summary_exposes_visual_metric_hierarchy(window) -> None:
    cards = window.dashboard.portfolio_summary._cards

    assert cards["Equity"].property("emphasis") == "primary"
    assert cards["Total P/L"].property("emphasis") == "primary"
    assert cards["Exposure"].property("emphasis") == "standard"
    assert cards["Buying Power"].property("emphasis") == "medium"
    assert cards["Open Positions"]._value.objectName() == "metricValue"


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
        "Mission Timeline",
        "Lifecycle",
        "System Health",
        "Portfolio Intelligence",
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


def test_market_workspace_uses_selected_atlas_candidate_for_intelligence(
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
                        rank="1",
                ),
            )
        )
    )

    assert workspace.chart_view is None
    assert workspace.trade_intelligence._symbol.text() == "AAPL"
    assert workspace.trade_intelligence._market_values["Bid"].text() == "$100.90"
    assert workspace.watchlist._table.item(0, 1).text() == "\u25cf AAPL"
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
            broker_account=BrokerNeutralAccountInformation(
                account_id="******ount",
                account_type="CASH",
                account_status="ACTIVE",
                buying_power=Decimal("9000"),
                cash_balance=Decimal("8000"),
                equity=Decimal("10500"),
                currency="USD",
            ),
                health_projection=HealthState(
                    runtime_status="RUNNING",
                    broker_status="CONNECTED",
                    market_data_status="CONNECTED",
                    scanner_status="RUNNING",
                    supported_symbols=7300,
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

    mission_runtime = window.dashboard.mission_status._values["System Health"]
    assert "Healthy" in mission_runtime.text()
    assert "Running" in (
        window.dashboard.mission_status._values["AI Scanner"].text()
    )
    assert window.dashboard.runtime_header._metrics["System Health"].text() == (
        "HEALTHY"
    )
    assert "Feed Connected" in window.global_status.data_feed.text()
    assert "Broker Connected" in window.global_status.broker.text()
    assert "Running" in (
        window.dashboard.market_workspace.atlas_activity
        ._rows["Evaluating"].text()
    )
    assert "0" in (
        window.dashboard.market_workspace.atlas_activity
        ._rows["Candidates"].text()
    )
    assert (
        window.dashboard.portfolio_summary._cards["Total P/L"]
        ._value.text()
        == "+$250.00"
    )
    assert (
        window.dashboard.portfolio_summary._cards["Equity"]._value.text()
        == "$10,500.00"
    )
    assert (
        window.dashboard.portfolio_summary._cards["Buying Power"]._value.text()
        == "$9,000.00"
    )
    assert (
        window.dashboard.portfolio_summary._cards["Exposure"]._value.text()
        == "11.4%"
    )
    assert window.dashboard.market_workspace.trade_intelligence._symbol.text() == "--"
    assert window.global_status.broker.text() == "\u25cf  Broker Connected"


def test_laptop_opportunities_and_intelligence_own_visible_workspace(application, window) -> None:
    window.resize(1280, 720)
    window.show()
    for _ in range(3):
        application.processEvents()

    market = window.dashboard.market_workspace
    assert market.layout_mode == "compact"
    assert market.focus_section.width() >= 400
    assert market.market_section.width() > market.focus_section.width()
    assert market.splitter.widget(0) is market.left_column
    assert market.splitter.widget(1) is market.right_workspace
    assert market.splitter.count() == 2
    assert window.intelligence_inspector.isHidden()


def test_splitter_handles_and_user_sizes_survive_ordinary_resize(
    application,
) -> None:
    workspace = MarketWorkspace()
    workspace.resize(1700, 900)
    workspace.show()
    application.processEvents()
    for splitter in (
        workspace.splitter,
        workspace.top_splitter,
        workspace.right_splitter,
    ):
        assert splitter.handleWidth() >= 6

    workspace.splitter.setSizes((430, 450))
    chosen = workspace.splitter.sizes()
    workspace.resize(1800, 900)
    application.processEvents()
    assert workspace.layout_mode == "wide"
    resized = workspace.splitter.sizes()
    assert abs(resized[0] / sum(resized) - chosen[0] / sum(chosen)) < 0.02
    workspace.render(WatchlistSnapshot())
    application.processEvents()
    assert workspace.splitter.sizes() == resized


def test_chart_focus_control_is_removed_from_primary_workspace(application) -> None:
    workspace = MarketWorkspace()
    workspace.resize(1700, 900)
    workspace.show()
    application.processEvents()

    assert workspace.chart_focused is False
    assert not workspace.focus_section.isHidden()
    assert not hasattr(workspace, "focus_chart_button")


def test_secondary_inspector_is_hidden_by_default_and_user_controlled(
    application, window
) -> None:
    window.resize(1366, 768)
    window.show()
    application.processEvents()

    inspector = window.intelligence_inspector
    button = window.dashboard.runtime_header.inspector_button
    assert inspector.isHidden()
    assert not button.isChecked()
    assert inspector.widget() is window.dashboard.market_workspace.intelligence_rail

    button.click()
    application.processEvents()
    assert inspector.isVisible()
    assert button.isChecked()

    inspector.close()
    application.processEvents()
    assert inspector.isHidden()
    assert not button.isChecked()


def test_compact_opportunity_selector_avoids_horizontal_scrolling(application) -> None:
    workspace = MarketWorkspace()
    row = WatchlistRow(
        symbol="XYZ", selected=True, latest_price="10.00", change="+1.00",
        change_percent="+10.00%", bid="9.99", ask="10.01", volume="100000",
        market_status="OPEN", last_update="10:00:00", stale="LIVE", rank="1",
        strategy_status="QUALIFYING", relative_volume="3.2", float_shares="5M",
        dollar_volume="$1M", catalyst="EARNINGS",
    )
    workspace.render(WatchlistSnapshot(rows=(row,), candidate_count=1))
    workspace.watchlist.resize(760, 240)
    workspace.watchlist.show()
    application.processEvents()

    table = workspace.watchlist._table
    assert table.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert table.horizontalScrollBar().maximum() == 0
    assert all(
        table.horizontalHeader().sectionResizeMode(index)
        == QHeaderView.ResizeMode.Interactive
        for index in range(table.columnCount())
    )


def test_qsettings_restores_splitters_and_sidebar(application, tmp_path) -> None:
    settings_path = tmp_path / "persist.ini"
    first_composition = create_desktop_composition()
    first = MainWindow(
        first_composition.bus,
        first_composition.state_store,
        first_composition.runtime_service,
        first_composition.trading_service,
        first_composition.order_command_factory,
        settings=QSettings(str(settings_path), QSettings.Format.IniFormat),
    )
    first.resize(1280, 720)
    first.show()
    application.processEvents()
    first._set_sidebar_compact(True)
    first.dashboard.market_workspace.splitter.setSizes((360, 220, 520))
    expected = first.dashboard.market_workspace.splitter.sizes()
    first.dashboard.market_workspace.right_splitter.setSizes((170, 210, 130, 190))
    expected_rail = first.dashboard.market_workspace.right_splitter.sizes()
    first.close()
    first_composition.close()

    second_composition = create_desktop_composition()
    second = MainWindow(
        second_composition.bus,
        second_composition.state_store,
        second_composition.runtime_service,
        second_composition.trading_service,
        second_composition.order_command_factory,
        settings=QSettings(str(settings_path), QSettings.Format.IniFormat),
    )
    second.show()
    application.processEvents()
    assert second.sidebar.compact is True
    restored_primary = second.dashboard.market_workspace.splitter.sizes()
    assert abs(
        restored_primary[0] / sum(restored_primary)
        - expected[0] / sum(expected)
    ) < 0.12
    restored_rail = second.dashboard.market_workspace.right_splitter.sizes()
    assert len(restored_rail) == len(expected_rail)
    assert all(abs(actual - expected) <= 5 for actual, expected in zip(
        restored_rail, expected_rail
    ))
    second.close()
    second_composition.close()


def test_malformed_geometry_falls_back_and_reset_layout_clears_settings(
    application, tmp_path
) -> None:
    settings = QSettings(
        str(tmp_path / "malformed.ini"), QSettings.Format.IniFormat
    )
    settings.setValue("layout/window_geometry", QByteArray(b"not-qt-geometry"))
    settings.setValue("layout/sidebar_compact", True)
    composition = create_desktop_composition()
    window = MainWindow(
        composition.bus,
        composition.state_store,
        composition.runtime_service,
        composition.trading_service,
        composition.order_command_factory,
        settings=settings,
    )
    assert window.size().toTuple() == (1440, 900)
    window.dashboard.market_workspace.set_chart_focus(True)
    window.reset_layout()
    assert window.dashboard.market_workspace.chart_focused is False
    assert window.sidebar.compact is False
    assert not settings.contains("layout/window_geometry")
    window.close()
    composition.close()


def test_repeated_window_open_close_cycles_destroy_native_widgets_cleanly(
    application, tmp_path
) -> None:
    for cycle in range(8):
        composition = create_desktop_composition()
        window = MainWindow(
            composition.bus,
            composition.state_store,
            composition.runtime_service,
            composition.trading_service,
            composition.order_command_factory,
            chart_market_data_service=composition.chart_market_data_service,
            settings=QSettings(
                str(tmp_path / f"cycle-{cycle}.ini"),
                QSettings.Format.IniFormat,
            ),
        )
        window.resize(1280, 720)
        window.show()
        application.processEvents()
        window.dashboard.runtime_header.inspector_button.click()
        application.processEvents()
        window.close()
        application.processEvents()
        application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        application.processEvents()
        assert composition.close()
