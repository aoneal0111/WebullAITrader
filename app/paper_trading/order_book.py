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
        # Authoritative lifecycle indexes over ``_orders``.  The global
        # index preserves open_orders() insertion order; the symbol index
        # keeps event-time lookup independent of retained terminal history.
        self._active_order_ids: dict[str, None] = {}
        self._active_order_ids_by_symbol: dict[str, dict[str, None]] = {}

    def submit(self, order: PaperOrder) -> PaperOrder:
        if order.order_id in self._orders:
            raise DuplicateOrderError(
                f"order {order.order_id} already exists"
            )

        self._orders[order.order_id] = order
        self._index(order)
        return order

    def restore(self, order: PaperOrder) -> PaperOrder:
        """Hydrate an authoritative order recovered from durable PAPER state."""
        if order.order_id in self._orders:
            current = self._orders[order.order_id]
            if current != order:
                raise OrderBookError(f"conflicting restored order {order.order_id}")
            return current
        self._orders[order.order_id] = order
        self._index(order)
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
        self._index(order, previous=current)
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
            self._orders[order_id]
            for order_id in self._active_order_ids
        )

    def open_orders_for_symbol(
        self,
        symbol: str,
    ) -> tuple[PaperOrder, ...]:
        normalized = self._normalize_symbol(symbol)

        order_ids = self._active_order_ids_by_symbol.get(normalized, {})
        return tuple(self._orders[order_id] for order_id in order_ids)

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

    def _index(
        self,
        order: PaperOrder,
        *,
        previous: PaperOrder | None = None,
    ) -> None:
        """Synchronize active indexes with one authoritative order."""

        symbol = self._normalize_symbol(order.request.symbol)
        if previous is not None and not previous.is_terminal:
            previous_symbol = self._normalize_symbol(previous.request.symbol)
            if previous_symbol != symbol:
                self._remove_active(order.order_id, previous_symbol)

        if order.is_terminal:
            self._remove_active(order.order_id, symbol)
            return

        self._active_order_ids[order.order_id] = None
        self._active_order_ids_by_symbol.setdefault(symbol, {})[
            order.order_id
        ] = None

    def _remove_active(self, order_id: str, symbol: str) -> None:
        self._active_order_ids.pop(order_id, None)
        symbol_orders = self._active_order_ids_by_symbol.get(symbol)
        if symbol_orders is None:
            return
        symbol_orders.pop(order_id, None)
        if not symbol_orders:
            self._active_order_ids_by_symbol.pop(symbol, None)

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        normalized = str(symbol).strip().upper()

        if not normalized:
            raise ValueError("symbol is required")

        return normalized

    @staticmethod
    def _normalize_order_id(order_id: str) -> str:
        normalized = str(order_id).strip()
        if not normalized:
            raise ValueError("order_id is required")
        return normalized
