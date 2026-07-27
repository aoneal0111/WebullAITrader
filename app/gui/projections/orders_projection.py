from __future__ import annotations

from app.gui.models import OrdersSnapshot
from app.operations_core import OperationsOrder


def project_orders(
    orders: tuple[OperationsOrder, ...],
) -> OrdersSnapshot:
    return OrdersSnapshot(
        rows=tuple(_project_order(order) for order in orders)
    )


def _project_order(
    order: OperationsOrder,
) -> tuple[str, str, str]:
    order_label = f"{order.side} {order.quantity} {order.symbol}"

    updated_label = order.updated_at.astimezone().strftime(
        "%Y-%m-%d %H:%M:%S %Z"
    )

    return (
        order_label,
        order.status,
        updated_label,
    )