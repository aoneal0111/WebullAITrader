from datetime import datetime, timezone

import pytest

from app.gui.formatters import format_orders
from app.gui.models import OrdersSnapshot
from app.gui.projections.dashboard_projection import project_dashboard
from app.operations_core import ApplicationState, OperationsOrder
from app.read_models.orders import (
    OrderReadModel,
    OrdersReadModelSnapshot,
)


NOW = datetime(2026, 7, 26, 15, 30, tzinfo=timezone.utc)


def make_read_model_order(
    *,
    order_id: str = "order-1",
    symbol: str = "AAPL",
    side: str = "BUY",
    quantity: str = "10",
    status: str = "ACCEPTED",
    order_type: str | None = None,
    limit_price: str | None = None,
    stop_price: str | None = None,
    filled_quantity: str | None = None,
    remaining_quantity: str | None = None,
    average_fill_price: str | None = None,
) -> OrderReadModel:
    return OrderReadModel(
        order_id=order_id,
        symbol=symbol,
        side=side,
        quantity=quantity,
        status=status,
        updated_at=NOW,
        order_type=order_type,
        limit_price=limit_price,
        stop_price=stop_price,
        filled_quantity=filled_quantity,
        remaining_quantity=remaining_quantity,
        average_fill_price=average_fill_price,
    )


def make_operations_order() -> OperationsOrder:
    return OperationsOrder(
        order_id="order-1",
        symbol="AAPL",
        side="BUY",
        quantity="10",
        status="ACCEPTED",
        updated_at=NOW,
    )


def test_format_orders_returns_empty_snapshot() -> None:
    snapshot = format_orders(
        OrdersReadModelSnapshot.initial()
    )

    assert snapshot == OrdersSnapshot.initial()


def test_format_orders_creates_dashboard_rows() -> None:
    first = make_read_model_order(
        order_type="LIMIT", limit_price="101.25",
        filled_quantity="0", remaining_quantity="10",
    )
    second = make_read_model_order(
        order_id="order-2",
        symbol="MSFT",
        side="SELL",
        quantity="5",
        status="PARTIALLY_FILLED",
        order_type="STOP", stop_price="99.50",
        filled_quantity="2", remaining_quantity="3",
        average_fill_price="99.45",
    )

    snapshot = format_orders(
        OrdersReadModelSnapshot(
            orders=(first, second),
        )
    )

    assert snapshot.rows[0] == (
        "MSFT", "SELL", "STOP", "5", "2", "3",
        "\u2014", "99.50", "99.45", "PARTIALLY_FILLED",
    )
    assert snapshot.rows[1] == (
        "AAPL", "BUY", "LIMIT", "10", "0", "10",
        "101.25", "\u2014", "\u2014", "ACCEPTED",
    )


def test_format_orders_preserves_immutable_rows() -> None:
    snapshot = format_orders(
        OrdersReadModelSnapshot(
            orders=(make_read_model_order(),),
        )
    )

    assert isinstance(snapshot.rows, tuple)
    assert isinstance(snapshot.rows[0], tuple)


def test_format_orders_bounds_mission_control_history() -> None:
    orders = tuple(
        make_read_model_order(
            order_id=f"order-{index}",
            symbol=f"S{index}",
            status="FILLED",
        )
        for index in range(30)
    )

    snapshot = format_orders(OrdersReadModelSnapshot(orders=orders))

    assert len(snapshot.rows) == 25


def test_format_orders_never_bounds_working_orders() -> None:
    orders = tuple(
        make_read_model_order(order_id=f"order-{index}", symbol=f"S{index}")
        for index in range(30)
    )

    snapshot = format_orders(OrdersReadModelSnapshot(orders=orders))

    assert len(snapshot.rows) == 30


def test_format_orders_rejects_wrong_model() -> None:
    with pytest.raises(
        TypeError,
        match="snapshot must be an OrdersReadModelSnapshot",
    ):
        format_orders(object())  # type: ignore[arg-type]


def test_dashboard_projects_orders_through_read_model() -> None:
    state = ApplicationState(
        orders=(make_operations_order(),),
    )

    snapshot = project_dashboard(state)

    assert snapshot.orders.rows[0] == (
        "AAPL", "BUY", "\u2014", "10", "\u2014",
        "\u2014", "\u2014", "\u2014", "\u2014", "ACCEPTED",
    )
