from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from app.gui.formatters import format_orders, format_positions
from app.gui.models import AIThinkingSnapshot
from app.gui.pages.dashboard import DashboardPage
from app.gui.pages.orders import OrdersPage
from app.gui.presenters import PositionsPresenter
from app.gui.projections.dashboard_projection import project_dashboard
from app.gui.projections.mission_control_projection import project_atlas_reasoning
from app.gui.shell.sidebar import Sidebar
from app.operations.runtime import PaperRuntimeEvent
from app.operations_core import ApplicationState, OperationsBus
from app.paper_trading.models import PaperFill
from app.read_models.position_projection import PositionProjection
from app.read_models.orders import OrderReadModel, OrdersReadModelSnapshot
from app.read_models.positions import PositionReadModel, PositionsReadModelSnapshot


NOW = datetime(2026, 9, 3, 18, 30, tzinfo=UTC)


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def _position(*, quantity: str = "250") -> PositionReadModel:
    return PositionReadModel(
        account_id="paper-account", symbol="PMI", asset_type="EQUITY",
        quantity=quantity, average_cost="5.09",
        market_value="1125" if quantity == "250" else "1800",
        unrealized_gain_loss="-147.50" if quantity == "250" else "-236",
        realized_gain_loss="60",
        currency="USD", updated_at=NOW,
    )


def _stop(**changes) -> OrderReadModel:
    values = dict(
        order_id="paper-stop-1", symbol="PMI", side="SELL", quantity="400",
        status="PARTIALLY_FILLED", updated_at=NOW, order_type="STOP",
        stop_price="5.03", filled_quantity="150", remaining_quantity="250",
        average_fill_price="4.50", submitted_at=NOW,
        lifecycle_id="WARRIOR|PMI|HIGH_OF_DAY_BREAKOUT",
        execution_reason="PROTECTIVE_STOP", execution_source="paper-order-gateway",
    )
    values.update(changes)
    return OrderReadModel(**values)


def _fill_event(*, sequence: int, side: str, quantity: str) -> PaperRuntimeEvent:
    fill_quantity = Decimal(quantity)
    fill_price = Decimal("5.09") if side == "BUY" else Decimal("4.50")
    fill = PaperFill(
        request_id=f"pmi-{sequence}", symbol="PMI", side=side,
        quantity=fill_quantity, fill_price=fill_price,
        notional=fill_quantity * fill_price,
        realized_pnl=Decimal("0") if side == "BUY" else Decimal("-88.50"),
        timestamp=NOW,
    )
    return PaperRuntimeEvent(
        sequence=sequence, timestamp=NOW, event_type="FILL",
        message=f"Projected {side} fill", cycle=sequence, symbol="PMI",
        fill=fill, mark_price=Decimal("4.50"),
    )


class _PositionsPanelSpy:
    def __init__(self) -> None:
        self.snapshot = None

    def render(self, snapshot) -> None:
        self.snapshot = snapshot


def test_authoritative_partial_exit_keeps_position_and_order_quantities_distinct() -> None:
    projection = PositionProjection(OperationsBus(), account_id="paper-account")
    projection(_fill_event(sequence=1, side="BUY", quantity="400"))
    assert projection.snapshot.positions[0].quantity == "400"
    initial_dashboard = project_dashboard(ApplicationState(
        position_projection=projection.snapshot,
    ))
    assert initial_dashboard.positions.management[0].quantity == "400"
    assert initial_dashboard.positions.rows[0][2] == "400"

    projection(_fill_event(sequence=2, side="SELL", quantity="150"))
    assert projection.snapshot.positions[0].quantity == "250"

    stop = _stop()
    state = ApplicationState(
        position_projection=projection.snapshot,
        order_projection=OrdersReadModelSnapshot((stop,)),
    )
    dashboard = project_dashboard(state)
    mission_position = dashboard.positions.management[0]
    assert mission_position.quantity == "250"
    assert dashboard.positions.rows[0][2] == "250"
    assert mission_position.protection is not None
    assert mission_position.protection.remaining_quantity == "250"
    assert stop.quantity == "400"
    assert stop.filled_quantity == "150"
    assert stop.remaining_quantity == "250"

    positions_page = _PositionsPanelSpy()
    PositionsPresenter(positions_page).render(state)  # type: ignore[arg-type]
    assert positions_page.snapshot.management[0].quantity == "250"
    assert positions_page.snapshot.rows[0][2] == "250"

    reasoning_text = " ".join((
        dashboard.atlas_reasoning.current_action,
        dashboard.atlas_reasoning.why,
        dashboard.atlas_reasoning.risk_protection,
        dashboard.atlas_reasoning.next_trigger,
    ))
    assert "250 remaining" in reasoning_text
    assert "managing 400 shares" not in reasoning_text.lower()


def test_position_is_prominent_and_protection_is_visually_correlated(application) -> None:
    snapshot = format_positions(
        PositionsReadModelSnapshot((_position(),)),
        OrdersReadModelSnapshot((_stop(),)),
    )
    page = DashboardPage()
    page.resize(1366, 768)
    page.show()
    page.positions_panel.render(snapshot)
    application.processEvents()

    management = snapshot.management[0]
    assert management.protection is not None
    assert management.protection.remaining_quantity == "250"
    assert page.market_workspace.positions_section.height() > (
        page.market_workspace.opportunities_section.height()
    )
    assert page.positions_panel._protection_status.text() == "PARTIALLY FILLED"
    assert "250 REMAINING" in page.positions_panel._protection_detail.text()
    assert "STOP 5.03" in page.positions_panel._protection_detail.text()


@pytest.mark.parametrize(
    "changes",
    (
        {"lifecycle_id": None},
        {"execution_reason": None},
        {"symbol": "OTHER"},
        {"side": "BUY"},
        {"status": "FILLED", "remaining_quantity": "0"},
    ),
)
def test_protection_correlation_never_invents_without_complete_evidence(changes) -> None:
    snapshot = format_positions(
        PositionsReadModelSnapshot((_position(),)),
        OrdersReadModelSnapshot((_stop(**changes),)),
    )
    assert snapshot.management[0].protection is None
    assert snapshot.management[0].management_state == "Protection not evidenced"


def test_atlas_reasoning_is_compact_observational_position_management(application) -> None:
    positions = format_positions(
        PositionsReadModelSnapshot((_position(),)),
        OrdersReadModelSnapshot((_stop(),)),
    )
    reasoning = project_atlas_reasoning(AIThinkingSnapshot(
        objective="Managing Active Positions",
        operational_state="Managing active positions.",
        reasoning="Position observation reports stop management active.",
        next_evaluation="Unknown",
        tone="good",
    ), positions)
    page = DashboardPage()
    page.market_workspace.render_atlas_reasoning(reasoning)

    assert page.market_workspace.reasoning_section.parent() is not None
    assert page.market_workspace.atlas_reasoning.current_action.text() == (
        "Managing active positions."
    )
    assert page.market_workspace.atlas_reasoning.why.text() == (
        "Position observation reports stop management active."
    )
    assert "250 remaining" in page.market_workspace.atlas_reasoning.risk_protection.text()
    assert "no projected trigger" in page.market_workspace.atlas_reasoning.next_trigger.text()
    assert page.market_workspace.ai_thinking_section.parent() is not None


def test_reasoning_and_management_rendering_never_read_sqlite_or_execute(
    application, monkeypatch
) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("rendering attempted SQLite or execution access")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    positions = format_positions(
        PositionsReadModelSnapshot((_position(),)),
        OrdersReadModelSnapshot((_stop(),)),
    )
    page = DashboardPage()
    page.positions_panel.render(positions)
    page.market_workspace.render_atlas_reasoning(
        project_atlas_reasoning(AIThinkingSnapshot(), positions)
    )


def test_trade_intelligence_remains_available_with_reduced_primary_footprint(
    application,
) -> None:
    page = DashboardPage()
    page.resize(1600, 900)
    page.show()
    application.processEvents()
    workspace = page.market_workspace
    assert workspace.market_section.isVisible()
    assert workspace.trade_intelligence.isVisible()
    assert workspace.left_column.width() > workspace.market_section.width()
    assert workspace.positions_section.isVisible()
    assert workspace.orders_section.isVisible()
    assert workspace.portfolio_section.maximumHeight() == 116


def test_orders_table_is_dense_and_detail_preserves_full_timestamp(application) -> None:
    page = OrdersPage()
    page.render_projection(OrdersReadModelSnapshot((_stop(limit_price=None),)))
    application.processEvents()

    assert page._orders_table.item(0, 6).text() == "\u2014"
    assert page._orders_table.item(0, 7).text() == "5.03"
    assert page._orders_table.item(0, 10).text() == NOW.astimezone().strftime(
        "%m/%d %H:%M:%S"
    )
    full = NOW.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    assert page._orders_table.item(0, 10).toolTip() == full
    assert page._submitted_at.text() == full
    assert page._updated_at.text() == full
    assert page._lifecycle_id.text() == "WARRIOR|PMI|HIGH_OF_DAY_BREAKOUT"
    assert page._execution_source.text() == "paper-order-gateway / PROTECTIVE_STOP"
    assert page._side.text() == "SELL"
    assert page._order_type.text() == "STOP"
    assert page._requested_quantity.text() == "400"
    assert page._filled_quantity.text() == "150"
    assert page._remaining_quantity.text() == "250"
    assert page._detail_status.text() == "PARTIALLY_FILLED"


def test_existing_table_values_are_never_replaced_by_null_glyph() -> None:
    row = format_orders(OrdersReadModelSnapshot((_stop(),))).rows[0]
    assert row[2:10] == (
        "STOP", "400", "150", "250", "\u2014", "5.03", "4.50",
        "PARTIALLY_FILLED",
    )


def test_compact_sidebar_routes_are_discoverable_and_home_remains_reachable() -> None:
    sidebar = Sidebar()
    sidebar.set_compact(True)
    assert sidebar.buttons["Mission Control"].text() != "MC"
    for label in sidebar.ITEMS:
        button = sidebar.buttons[label]
        assert button.toolTip() == label
        assert button.accessibleName() == label
        assert button.accessibleDescription() == f"Open {label}"
    requested = []
    sidebar.page_requested.connect(requested.append)
    sidebar.buttons["Mission Control"].click()
    assert requested[-1] == 0
