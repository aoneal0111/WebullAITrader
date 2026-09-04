from __future__ import annotations

import os
from datetime import UTC, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from app.composition import create_desktop_composition
from app.gui.main_window import MainWindow
from app.gui.pages.orders import OrdersPage
from app.gui.models import PositionsSnapshot, WatchlistSnapshot
from app.operations_core import OperationsOrder
from app.read_models.orders import OrderReadModel, OrdersReadModelSnapshot


NOW = datetime(2026, 9, 3, 18, 0, tzinfo=UTC)


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
            str(tmp_path / "gui-redesign.ini"), QSettings.Format.IniFormat
        ),
    )
    desktop.resize(1366, 768)
    desktop.show()
    application.processEvents()
    yield desktop
    desktop.close()
    composition.close()


def test_sidebar_is_mounted_and_every_major_route_returns_home(
    application, window
) -> None:
    assert window.centralWidget().layout().indexOf(window.sidebar) == 0
    assert window.sidebar.isVisible()

    for label, route in zip(window.sidebar.ITEMS, window.sidebar.ROUTES):
        window.sidebar.buttons[label].click()
        application.processEvents()
        assert window.pages.currentIndex() == route
        assert window.sidebar.buttons[label].isChecked()
        window.sidebar.buttons["Mission Control"].click()
        assert window.pages.currentIndex() == 0


def test_orders_and_multi_page_navigation_are_not_dead_ends(
    application, window
) -> None:
    for label in ("Orders", "Positions", "Orders", "Decisions", "Mission Control"):
        window.sidebar.buttons[label].click()
        application.processEvents()
    assert window.pages.currentIndex() == 0
    assert window.sidebar.buttons["Mission Control"].isChecked()

    window.pages.setCurrentIndex(8)
    assert window.sidebar.buttons["Replay"].isChecked()
    window.pages.setCurrentIndex(4)
    assert window.sidebar.buttons["System / Settings"].isChecked()


def test_orders_page_is_read_only_by_default_and_renders_authoritative_fields(
    application,
) -> None:
    page = OrdersPage()
    assert page.order_entry_panel.isHidden()
    assert page.layout().indexOf(page.order_entry_panel) == -1

    page.render_projection(OrdersReadModelSnapshot(orders=(OrderReadModel(
        order_id="paper-stop-1",
        symbol="PMI",
        side="SELL",
        quantity="400",
        status="PARTIALLY_FILLED",
        updated_at=NOW,
        order_type="STOP",
        limit_price=None,
        stop_price="5.03",
        filled_quantity="150",
        remaining_quantity="250",
        average_fill_price="4.50",
        submitted_at=NOW,
        lifecycle_id="WARRIOR|PMI|HIGH_OF_DAY_BREAKOUT",
        execution_reason="STOP",
        execution_source="paper-order-gateway",
    ),)))
    application.processEvents()

    expected = (
        "PMI", "SELL", "400", "150", "250", "STOP",
        "\u2014", "5.03", "4.50", "PARTIALLY_FILLED",
    )
    assert tuple(page._orders_table.item(0, index).text() for index in range(10)) == expected
    assert page._lifecycle_id.text() == "WARRIOR|PMI|HIGH_OF_DAY_BREAKOUT"
    assert page._execution_source.text() == "paper-order-gateway / STOP"


def test_reduced_historical_order_contract_remains_valid() -> None:
    reduced = OperationsOrder(
        order_id="legacy-1", symbol="AAPL", side="BUY", quantity="10",
        status="ACCEPTED", updated_at=NOW,
    )
    assert reduced.order_type is None
    assert reduced.remaining_quantity is None


def test_mission_control_prioritizes_positions_orders_and_compact_account(
    application, window
) -> None:
    # QtStateBridge coalesces the store's initial revision behind its timer.
    # Flush that canonical state before directly exercising independent widget
    # renders, so a later processEvents() cannot deliver an older empty snapshot.
    window._state_bridge._flush()
    application.processEvents()

    workspace = window.dashboard.market_workspace
    assert workspace.positions_section.isVisible()
    assert workspace.orders_section.isVisible()
    assert workspace.market_overview_section.parentWidget() is None
    assert tuple(workspace.portfolio_summary._cards) == (
        "Equity", "Cash", "Buying Power", "Unrealized P/L",
        "Realized P/L", "Open Positions", "Exposure", "Working Orders",
    )
    assert workspace.portfolio_section.height() < workspace.workspace_splitter.height()

    workspace.positions_panel.render(PositionsSnapshot(rows=((
        "PMI", "LONG", "400", "$5.09", "$4.50", "-$236.00",
        "-11.59%", "$0.00", "13:10:04",
    ),)))
    workspace.render(WatchlistSnapshot())
    application.processEvents()
    assert workspace.positions_panel._table.item(0, 0).text() == "PMI"


def test_projection_rendering_does_not_call_execution_services() -> None:
    class Forbidden:
        def __getattr__(self, name):
            raise AssertionError(f"rendering accessed execution service: {name}")

    page = OrdersPage(Forbidden(), Forbidden())
    page.render_projection(OrdersReadModelSnapshot())
    assert page._orders_table.rowCount() == 1


def test_canonical_empty_projection_clears_obsolete_order_rows(application) -> None:
    page = OrdersPage()
    page.render_projection(OrdersReadModelSnapshot(orders=(OrderReadModel(
        order_id="chpt-entry", symbol="CHPT", side="BUY", quantity="1833",
        status="WORKING", updated_at=NOW, order_type="LIMIT",
        limit_price="9.104550", filled_quantity="0",
        remaining_quantity="1833",
    ),)))
    assert page._orders_table.item(0, 0).text() == "CHPT"

    page.render_projection(OrdersReadModelSnapshot.initial())
    application.processEvents()

    assert page._orders_table.rowCount() == 1
    assert "No active paper orders" in page._orders_table.item(0, 0).text()
