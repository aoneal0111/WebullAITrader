from __future__ import annotations

from app.operations_core import ApplicationState, OperationsOrder
from app.read_models.orders.models import (
    OrderReadModel,
    OrdersReadModelSnapshot,
)


def project_orders_read_model(
    state: ApplicationState,
) -> OrdersReadModelSnapshot:
    """Project authoritative application state into an orders read model."""

    if not isinstance(state, ApplicationState):
        raise TypeError("state must be an ApplicationState")

    if state.order_projection.orders or not state.orders:
        return state.order_projection

    # Compatibility for callers constructing ApplicationState with the
    # pre-projection ``orders`` field directly.
    return project_operational_orders(state.orders)


def project_operational_orders(
    orders: tuple[OperationsOrder, ...],
) -> OrdersReadModelSnapshot:
    """Project an immutable operational-order collection."""

    if not isinstance(orders, tuple):
        raise TypeError("orders must be an immutable tuple")

    if any(
        not isinstance(order, OperationsOrder)
        for order in orders
    ):
        raise TypeError(
            "orders must contain only OperationsOrder instances"
        )

    return OrdersReadModelSnapshot(
        orders=tuple(
            _project_order(order)
            for order in orders
        )
    )


def _project_order(order: OperationsOrder) -> OrderReadModel:
    return OrderReadModel(
        order_id=order.order_id,
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
        status=order.status,
        updated_at=order.updated_at,
    )
