from __future__ import annotations

from decimal import Decimal

from app.broker_protocol.models import BrokerOrder
from app.operations_core import OperationsOrder


def map_broker_orders(
    orders: tuple[BrokerOrder, ...],
) -> tuple[OperationsOrder, ...]:
    """Map live broker orders into backend-neutral operations state."""
    if not isinstance(orders, tuple):
        raise TypeError("orders must be an immutable tuple")
    if any(not isinstance(order, BrokerOrder) for order in orders):
        raise TypeError("orders must contain only BrokerOrder instances")

    return tuple(
        _map_broker_order(order)
        for order in sorted(
            orders,
            key=lambda item: (
                item.updated_timestamp,
                item.broker_order_id,
            ),
            reverse=True,
        )
    )


def _map_broker_order(order: BrokerOrder) -> OperationsOrder:
    return OperationsOrder(
        order_id=order.broker_order_id.strip(),
        symbol=order.symbol.strip().upper(),
        side=order.side.value,
        quantity=_decimal_text(order.quantity),
        status=order.status.value,
        updated_at=order.updated_timestamp,
        order_type=order.order_type.value,
        limit_price=_optional_decimal_text(order.limit_price),
        stop_price=_optional_decimal_text(order.stop_price),
        filled_quantity=_decimal_text(order.filled_quantity),
        remaining_quantity=_decimal_text(
            order.quantity - order.filled_quantity
        ),
        execution_source="LIVE_BROKER",
    )


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else _decimal_text(value)


__all__ = ["map_broker_orders"]
