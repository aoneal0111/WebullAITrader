from __future__ import annotations

from app.gui.models import OrdersSnapshot
from app.read_models.orders import OrdersReadModelSnapshot


def format_orders(
    snapshot: OrdersReadModelSnapshot,
) -> OrdersSnapshot:
    """Format an orders read model for the dashboard orders panel."""

    if not isinstance(snapshot, OrdersReadModelSnapshot):
        raise TypeError(
            "snapshot must be an OrdersReadModelSnapshot"
        )

    return OrdersSnapshot(
        rows=tuple(
            _format_order(
                side=order.side,
                quantity=order.quantity,
                symbol=order.symbol,
                status=order.status,
                updated_at=order.updated_at,
            )
            for order in snapshot.orders
        )
    )


def _format_order(
    *,
    side: str,
    quantity: str,
    symbol: str,
    status: str,
    updated_at,
) -> tuple[str, str, str]:
    order_label = f"{side} {quantity} {symbol}"
    updated_label = updated_at.astimezone().strftime(
        "%Y-%m-%d %H:%M:%S %Z"
    )

    return (
        order_label,
        status,
        updated_label,
    )
