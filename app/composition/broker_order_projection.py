from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.broker_protocol.models import BrokerOrder
from app.composition.operations_order_mapper import map_broker_orders
from app.operations_core import (
    OperationsBus,
    OperationsOrder,
    OrdersUpdated,
    ProjectionAuthority,
)


BrokerOrdersSink = Callable[
    [tuple[BrokerOrder, ...], datetime],
    tuple[OperationsOrder, ...],
]


def create_broker_orders_publisher(
    bus: OperationsBus,
    *,
    source: str = "live-broker",
) -> BrokerOrdersSink:
    """Create a publisher that projects live broker orders onto OperationsBus."""
    if not isinstance(bus, OperationsBus):
        raise TypeError("bus must be an OperationsBus")

    normalized_source = source.strip()
    if not normalized_source:
        raise ValueError("source must not be empty")

    def publish_orders(
        orders: tuple[BrokerOrder, ...],
        occurred_at: datetime,
    ) -> tuple[OperationsOrder, ...]:
        mapped = map_broker_orders(orders)
        bus.publish(
            OrdersUpdated(
                source=normalized_source,
                orders=mapped,
                occurred_at=occurred_at,
                projection_authority=ProjectionAuthority.BROKER_CURRENT,
            )
        )
        return mapped

    return publish_orders


__all__ = [
    "BrokerOrdersSink",
    "create_broker_orders_publisher",
]
