from __future__ import annotations

from typing import Protocol

from app.operations_core import (
    OperationsBus,
    OperationsOrder,
    OrdersUpdated,
)
from app.paper_trading.order_models import PaperOrder


class PaperOrderHistory(Protocol):
    """Read-only paper-order history required by the GUI publisher."""

    def history(self) -> tuple[PaperOrder, ...]:
        ...


class PaperOrdersPublisher:
    """Publish the paper order book as Operations Center state."""

    def __init__(
        self,
        *,
        bus: OperationsBus,
        order_book: PaperOrderHistory,
    ) -> None:
        self._bus = bus
        self._order_book = order_book

    def publish(self) -> OrdersUpdated:
        """Publish the current complete paper-order snapshot."""

        event = OrdersUpdated(
            source="paper-orders",
            orders=tuple(
                self._project_order(order)
                for order in self._order_book.history()
            ),
        )

        self._bus.publish(event)
        return event

    def __call__(self, _runtime_result: object) -> OrdersUpdated:
        """Allow the publisher to be used as a runtime result sink."""

        return self.publish()

    @staticmethod
    def _project_order(order: PaperOrder) -> OperationsOrder:
        return OperationsOrder(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side.value,
            quantity=format(order.quantity, "f"),
            status=order.status.value,
            updated_at=order.updated_at,
        )
