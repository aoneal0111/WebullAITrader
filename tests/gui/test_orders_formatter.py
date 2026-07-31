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
) -> OrderReadModel:
    return OrderReadModel(
        order_id=order_id,
        symbol=symbol,
        side=side,
        quantity=quantity,
        status=status,
        updated_at=NOW,
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
    first = make_read_model_order()
    second = make_read_model_order(
        order_id="order-2",
        symbol="MSFT",
        side="SELL",
        quantity="5",
        status="PARTIALLY_FILLED",
    )

    snapshot = format_orders(
        OrdersReadModelSnapshot(
            orders=(first, second),
        )
    )

    assert snapshot.rows[0] == ("AAPL", "BUY", "--", "--", "10", "ACCEPTED")
    assert snapshot.rows[1] == (
        "MSFT", "SELL", "--", "--", "5", "PARTIALLY_FILLED"
    )


def test_format_orders_preserves_immutable_rows() -> None:
    snapshot = format_orders(
        OrdersReadModelSnapshot(
            orders=(make_read_model_order(),),
        )
    )

    assert isinstance(snapshot.rows, tuple)
    assert isinstance(snapshot.rows[0], tuple)


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
        "AAPL", "BUY", "--", "--", "10", "ACCEPTED"
    )
