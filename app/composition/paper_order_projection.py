from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from app.operations_core import (
    OperationsBus,
    OperationsOrder,
    OrdersUpdated,
    PaperOrderLifecycleUpdated,
)
from app.paper_trading.order_book import PaperOrderBook
from app.paper_trading.order_lifecycle import (
    OrderLifecycleEvent,
    PaperOrderLifecycleCoordinator,
)
from app.paper_trading.order_models import PaperOrder

PaperOrderLifecycleSink = Callable[[OrderLifecycleEvent], None]


def map_paper_orders(
    orders: tuple[PaperOrder, ...],
) -> tuple[OperationsOrder, ...]:
    """Map paper orders into backend-neutral operations state."""
    if not isinstance(orders, tuple):
        raise TypeError("orders must be an immutable tuple")
    if any(not isinstance(order, PaperOrder) for order in orders):
        raise TypeError("orders must contain only PaperOrder instances")

    return tuple(
        _map_paper_order(order)
        for order in sorted(
            orders,
            key=lambda item: (item.updated_at, item.order_id),
            reverse=True,
        )
    )


def create_paper_order_lifecycle_publisher(
    bus: OperationsBus,
    order_book: PaperOrderBook,
    *,
    source: str = "paper-order-lifecycle",
) -> PaperOrderLifecycleSink:
    """Create a lifecycle listener that republishes the order-book snapshot."""
    if not isinstance(bus, OperationsBus):
        raise TypeError("bus must be an OperationsBus")
    if not isinstance(order_book, PaperOrderBook):
        raise TypeError("order_book must be a PaperOrderBook")

    normalized_source = source.strip()
    if not normalized_source:
        raise ValueError("source must not be empty")

    def publish_order_lifecycle(event: OrderLifecycleEvent) -> None:
        if not isinstance(event, OrderLifecycleEvent):
            raise TypeError("event must be an OrderLifecycleEvent")
        history = order_book.history()
        matching_order = next(
            (
                order
                for order in reversed(history)
                if order.order_id == event.order_id
            ),
            None,
        )
        bus.publish(
            PaperOrderLifecycleUpdated(
                source=normalized_source,
                occurred_at=event.occurred_at,
                order_id=event.order_id,
                previous_status=event.previous_status.value,
                current_status=event.current_status.value,
                filled_quantity=event.filled_quantity,
                remaining_quantity=event.remaining_quantity,
                fill_price=event.fill_price,
                symbol=(
                    None
                    if matching_order is None
                    else matching_order.symbol
                ),
            )
        )
        bus.publish(
            OrdersUpdated(
                source=normalized_source,
                orders=map_paper_orders(history),
                occurred_at=event.occurred_at,
            )
        )

    return publish_order_lifecycle


def create_operations_paper_order_lifecycle_coordinator(
    bus: OperationsBus,
    order_book: PaperOrderBook,
    *,
    source: str = "paper-order-lifecycle",
    clock=None,
) -> PaperOrderLifecycleCoordinator:
    """Compose lifecycle execution with deterministic OperationsBus events."""
    publisher = create_paper_order_lifecycle_publisher(
        bus,
        order_book,
        source=source,
    )
    return PaperOrderLifecycleCoordinator(
        order_book,
        listeners=(publisher,),
        clock=clock,
    )


def _map_paper_order(order: PaperOrder) -> OperationsOrder:
    return OperationsOrder(
        order_id=order.order_id,
        symbol=order.symbol,
        side=order.side.value,
        quantity=_decimal_text(order.quantity),
        status=order.status.value,
        updated_at=order.updated_at,
    )


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


__all__ = [
    "PaperOrderLifecycleSink",
    "create_operations_paper_order_lifecycle_coordinator",
    "create_paper_order_lifecycle_publisher",
    "map_paper_orders",
]
