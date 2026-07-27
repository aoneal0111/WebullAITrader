from datetime import datetime, timezone

import pytest

from app.operations_core import ApplicationState, OperationsOrder
from app.read_models.orders.models import (
    OrderReadModel,
    OrdersReadModelSnapshot,
)
from app.read_models.orders.projector import (
    project_operational_orders,
    project_orders_read_model,
)


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def make_operations_order(
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


def test_empty_application_state_projects_initial_snapshot() -> None:
    snapshot = project_orders_read_model(ApplicationState())

    assert snapshot == OrdersReadModelSnapshot.initial()


def test_projector_preserves_operational_order_facts() -> None:
    source = make_operations_order()
    state = ApplicationState(orders=(source,))

    snapshot = project_orders_read_model(state)

    assert snapshot.orders == (
        OrderReadModel(
            order_id="order-1",
            symbol="AAPL",
            side="BUY",
            quantity="10",
            status="ACCEPTED",
            updated_at=NOW,
        ),
    )


def test_projector_preserves_source_ordering() -> None:
    first = make_operations_order()
    second = make_operations_order(
        order_id="order-2",
        symbol="MSFT",
        side="SELL",
        quantity="5",
        status="PARTIALLY_FILLED",
    )

    snapshot = project_operational_orders((first, second))

    assert tuple(
        order.order_id
        for order in snapshot.orders
    ) == ("order-1", "order-2")


def test_projection_does_not_reuse_mutable_collections() -> None:
    snapshot = project_operational_orders(
        (make_operations_order(),)
    )

    assert isinstance(snapshot.orders, tuple)


def test_projector_rejects_non_application_state() -> None:
    with pytest.raises(
        TypeError,
        match="state must be an ApplicationState",
    ):
        project_orders_read_model(object())  # type: ignore[arg-type]


def test_operational_projection_requires_tuple() -> None:
    with pytest.raises(
        TypeError,
        match="orders must be an immutable tuple",
    ):
        project_operational_orders(  # type: ignore[arg-type]
            [make_operations_order()]
        )
