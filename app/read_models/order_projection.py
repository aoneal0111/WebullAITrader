from __future__ import annotations

from threading import RLock

from app.operations.runtime import PaperRuntimeEvent
from app.operations_core import (
    OperationsBus,
    OperationsOrder,
    OrdersUpdated,
    ProjectionAuthority,
)
from app.read_models.orders.models import (
    OrderReadModel,
    OrdersReadModelSnapshot,
)
from app.read_models.runtime_event_identity import projection_event_id


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
                event_id=projection_event_id("orders", event),
                source="paper-runtime-order-projection",
                orders=orders,
                projection_authority=ProjectionAuthority.PAPER_EXECUTION,
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
        order_type=order.order_type,
        limit_price=order.limit_price,
        stop_price=order.stop_price,
        filled_quantity=order.filled_quantity,
        remaining_quantity=order.remaining_quantity,
        average_fill_price=order.average_fill_price,
        submitted_at=order.submitted_at,
        lifecycle_id=order.lifecycle_id,
        execution_reason=order.execution_reason,
        execution_source=order.execution_source,
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
        order_type=order.order_type,
        limit_price=order.limit_price,
        stop_price=order.stop_price,
        filled_quantity=order.filled_quantity,
        remaining_quantity=order.remaining_quantity,
        average_fill_price=order.average_fill_price,
        submitted_at=order.submitted_at,
        lifecycle_id=order.lifecycle_id,
        execution_reason=order.execution_reason,
        execution_source=order.execution_source,
    )


__all__ = ["OrderProjection"]
