from datetime import datetime, timezone

from app.gui.models import OrdersSnapshot
from app.gui.projections.dashboard_projection import project_dashboard
from app.gui.projections.orders_projection import project_orders
from app.operations_core import ApplicationState, OperationsOrder


NOW = datetime(2026, 7, 26, 15, 30, tzinfo=timezone.utc)


def make_order(
    *,
    order_id: str = "order-1",
    symbol: str = "AAPL",
    side: str = "BUY",
    quantity: str = "10",
    status: str = "ACCEPTED",
) -> OperationsOrder:
    return OperationsOrder(
        order_id=order_id,
        symbol=symbol,
        side=side,
        quantity=quantity,
        status=status,
        updated_at=NOW,
    )


def test_project_orders_returns_empty_snapshot_for_no_orders() -> None:
    snapshot = project_orders(())

    assert snapshot == OrdersSnapshot.initial()


def test_project_orders_formats_immutable_rows() -> None:
    first = make_order()
    second = make_order(
        order_id="order-2",
        symbol="MSFT",
        side="SELL",
        quantity="5",
        status="PARTIALLY_FILLED",
    )

    snapshot = project_orders((first, second))

    assert snapshot.rows[0][0] == "BUY 10 AAPL"
    assert snapshot.rows[0][1] == "ACCEPTED"
    assert snapshot.rows[1][0] == "SELL 5 MSFT"
    assert snapshot.rows[1][1] == "PARTIALLY_FILLED"

    assert snapshot.rows[0][2]
    assert snapshot.rows[1][2]


def test_dashboard_projection_uses_application_state_orders() -> None:
    order = make_order()

    state = ApplicationState(
        orders=(order,),
    )

    snapshot = project_dashboard(state)

    assert snapshot.orders.rows[0][0] == "BUY 10 AAPL"
    assert snapshot.orders.rows[0][1] == "ACCEPTED"