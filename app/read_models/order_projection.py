from __future__ import annotations

from threading import RLock

from app.operations.runtime import PaperRuntimeEvent
from app.operations_core import OperationsBus, OperationsOrder, OrdersUpdated
from app.read_models.orders.models import (
    OrderReadModel,
    OrdersReadModelSnapshot,
)


class OrderProjection:
    """Fold explicit runtime order facts into an immutable read model."""

    def __init__(self, bus: OperationsBus) -> None:
        if not isinstance(bus, OperationsBus):
            raise TypeError("bus must be an OperationsBus")

        self._bus = bus
        self._lock = RLock()
        self._snapshot = OrdersReadModelSnapshot.initial()

    @property
    def snapshot(self) -> OrdersReadModelSnapshot:
        with self._lock:
            return self._snapshot

    def __call__(self, event: PaperRuntimeEvent) -> None:
        if not isinstance(event, PaperRuntimeEvent):
            raise TypeError("event must be a PaperRuntimeEvent")
        if event.order is None:
            return

        with self._lock:
            current = self._snapshot
            projected = _reduce_order(current, event.order)
            if projected == current:
                return
            self._snapshot = projected
            orders = tuple(
                _to_operations_order(order)
                for order in projected.orders
            )

        self._bus.publish(
            OrdersUpdated(
                occurred_at=event.timestamp,
                source="paper-runtime-order-projection",
                orders=orders,
            )
        )


def _reduce_order(
    current: OrdersReadModelSnapshot,
    order: OperationsOrder,
) -> OrdersReadModelSnapshot:
    projected = OrderReadModel(
        order_id=order.order_id,
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
        status=order.status,
        updated_at=order.updated_at,
    )
    by_id = {
        item.order_id: item
        for item in current.orders
    }
    by_id[projected.order_id] = projected

    return OrdersReadModelSnapshot(
        orders=tuple(
            sorted(
                by_id.values(),
                key=lambda item: (item.updated_at, item.order_id),
                reverse=True,
            )
        )
    )


def _to_operations_order(order: OrderReadModel) -> OperationsOrder:
    return OperationsOrder(
        order_id=order.order_id,
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
        status=order.status,
        updated_at=order.updated_at,
    )


__all__ = ["OrderProjection"]
