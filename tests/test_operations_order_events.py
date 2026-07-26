from datetime import datetime, timezone

import pytest

from app.operations_core import OperationsOrder, OrdersUpdated


NOW = datetime(2026, 7, 26, 15, 30, tzinfo=timezone.utc)


def make_order() -> OperationsOrder:
    return OperationsOrder(
        order_id="order-1",
        symbol="AAPL",
        side="BUY",
        quantity="10",
        status="ACCEPTED",
        updated_at=NOW,
    )


def test_operations_order_accepts_valid_contract() -> None:
    order = make_order()

    assert order.order_id == "order-1"
    assert order.symbol == "AAPL"
    assert order.side == "BUY"
    assert order.quantity == "10"
    assert order.status == "ACCEPTED"
    assert order.updated_at == NOW


@pytest.mark.parametrize(
    "field_name",
    (
        "order_id",
        "symbol",
        "side",
        "quantity",
        "status",
    ),
)
def test_operations_order_rejects_blank_text(field_name: str) -> None:
    values = {
        "order_id": "order-1",
        "symbol": "AAPL",
        "side": "BUY",
        "quantity": "10",
        "status": "ACCEPTED",
        "updated_at": NOW,
    }
    values[field_name] = " "

    with pytest.raises(ValueError):
        OperationsOrder(**values)


def test_operations_order_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        OperationsOrder(
            order_id="order-1",
            symbol="AAPL",
            side="BUY",
            quantity="10",
            status="ACCEPTED",
            updated_at=datetime(2026, 7, 26, 15, 30),
        )


def test_orders_updated_accepts_immutable_tuple() -> None:
    order = make_order()

    event = OrdersUpdated(
        source="test-order-source",
        orders=(order,),
        occurred_at=NOW,
    )

    assert event.orders == (order,)


def test_orders_updated_rejects_mutable_sequence() -> None:
    with pytest.raises(TypeError, match="immutable tuple"):
        OrdersUpdated(
            orders=[make_order()],
            occurred_at=NOW,
        )


def test_orders_updated_rejects_invalid_tuple_members() -> None:
    with pytest.raises(
        TypeError,
        match="OperationsOrder instances",
    ):
        OrdersUpdated(
            orders=("not-an-order",),
            occurred_at=NOW,
        )