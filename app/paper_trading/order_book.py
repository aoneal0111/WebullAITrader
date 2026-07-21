from __future__ import annotations

from datetime import datetime

from app.paper_trading.order_models import (
    OrderStatus,
    PaperOrder,
    TimeInForce,
)
from app.paper_trading.orders import cancel_order, expire_order


class OrderBookError(RuntimeError):
    """Base error for paper order-book operations."""


class DuplicateOrderError(OrderBookError):
    """Raised when an order ID is submitted more than once."""


class OrderNotFoundError(OrderBookError):
    """Raised when an order ID is not present in the book."""


class StaleOrderUpdateError(OrderBookError):
    """Raised when an older order snapshot would replace a newer one."""


class PaperOrderBook:
    """In-memory authoritative store for immutable paper orders.

    The order book owns storage and lifecycle indexing only. It does not
    decide whether an order should fill, calculate prices, or update a
    portfolio.
    """

    def __init__(self) -> None:
        self._orders: dict[str, PaperOrder] = {}

    def submit(self, order: PaperOrder) -> PaperOrder:
        if order.order_id in self._orders:
            raise DuplicateOrderError(
                f"order {order.order_id} already exists"
            )

        self._orders[order.order_id] = order
        return order

    def update(self, order: PaperOrder) -> PaperOrder:
        current = self.get(order.order_id)

        if order.created_at != current.created_at:
            raise OrderBookError(
                "updated order must preserve created_at"
            )

        if order.updated_at < current.updated_at:
            raise StaleOrderUpdateError(
                f"order {order.order_id} update is stale"
            )

        self._orders[order.order_id] = order
        return order

    def get(self, order_id: str) -> PaperOrder:
        normalized = self._normalize_order_id(order_id)
        try:
            return self._orders[normalized]
        except KeyError as exc:
            raise OrderNotFoundError(
                f"order {normalized} was not found"
            ) from exc

    def contains(self, order_id: str) -> bool:
        normalized = self._normalize_order_id(order_id)
        return normalized in self._orders

    def cancel(
        self,
        order_id: str,
        *,
        at: datetime | None = None,
    ) -> PaperOrder:
        cancelled = cancel_order(self.get(order_id), at=at)
        return self.update(cancelled)

    def expire_day_orders(
        self,
        *,
        at: datetime | None = None,
    ) -> tuple[PaperOrder, ...]:
        expired: list[PaperOrder] = []

        for order in self.open_orders():
            if order.request.time_in_force is not TimeInForce.DAY:
                continue

            transitioned = expire_order(order, at=at)
            self.update(transitioned)
            expired.append(transitioned)

        return tuple(expired)

    def open_orders(self) -> tuple[PaperOrder, ...]:
        return tuple(
            order
            for order in self._orders.values()
            if not order.is_terminal
        )

    def terminal_orders(self) -> tuple[PaperOrder, ...]:
        return tuple(
            order
            for order in self._orders.values()
            if order.is_terminal
        )

    def history(self) -> tuple[PaperOrder, ...]:
        return tuple(self._orders.values())

    def __len__(self) -> int:
        return len(self._orders)

    @staticmethod
    def _normalize_order_id(order_id: str) -> str:
        normalized = str(order_id).strip()
        if not normalized:
            raise ValueError("order_id is required")
        return normalized
