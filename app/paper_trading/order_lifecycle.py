from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Callable, Mapping

from app.paper_trading.order_book import PaperOrderBook
from app.paper_trading.order_models import (
    OrderSide,
    OrderStatus,
    OrderType,
    PaperOrder,
    TimeInForce,
)
from app.paper_trading.orders import apply_fill, cancel_order

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class OrderLifecycleEvent:
    """Immutable notification emitted after an order lifecycle change."""

    order_id: str
    previous_status: OrderStatus
    current_status: OrderStatus
    occurred_at: datetime
    filled_quantity: Decimal
    remaining_quantity: Decimal
    fill_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class OrderEvaluation:
    """Result of evaluating one order against one market price."""

    executable: bool
    fill_price: Decimal | None
    reason: str


OrderLifecycleListener = Callable[[OrderLifecycleEvent], None]


class PaperOrderLifecycleCoordinator:
    """Advance paper orders deterministically from market observations.

    The coordinator owns execution orchestration only. The order book remains
    the authoritative store and the existing immutable transition functions
    remain the only mechanism used to change order state.
    """

    def __init__(
        self,
        order_book: PaperOrderBook,
        *,
        listeners: tuple[OrderLifecycleListener, ...] = (),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(order_book, PaperOrderBook):
            raise TypeError("order_book must be PaperOrderBook")
        if any(not callable(listener) for listener in listeners):
            raise TypeError("listeners must contain callables")

        self._order_book = order_book
        self._listeners = tuple(listeners)
        self._clock = clock if clock is not None else _utc_now

    @property
    def order_book(self) -> PaperOrderBook:
        return self._order_book

    def process_order(
        self,
        order_id: str,
        *,
        market_price: Decimal,
        available_quantity: Decimal | None = None,
        at: datetime | None = None,
    ) -> PaperOrder:
        """Evaluate and, when executable, fill one open paper order."""

        if market_price <= ZERO:
            raise ValueError("market_price must be positive")
        if available_quantity is not None and available_quantity < ZERO:
            raise ValueError("available_quantity cannot be negative")

        order = self._order_book.get(order_id)
        if order.is_terminal:
            return order

        timestamp = at if at is not None else self._clock()
        _require_aware(timestamp)

        evaluation = evaluate_order(order, market_price)
        if not evaluation.executable:
            if order.request.time_in_force is TimeInForce.IOC:
                return self._cancel_and_publish(order, timestamp)
            return order

        fill_quantity = order.remaining_quantity
        if available_quantity is not None:
            fill_quantity = min(fill_quantity, available_quantity)

        if fill_quantity <= ZERO:
            if order.request.time_in_force is TimeInForce.IOC:
                return self._cancel_and_publish(order, timestamp)
            return order

        assert evaluation.fill_price is not None
        updated = apply_fill(
            order,
            fill_quantity,
            evaluation.fill_price,
            at=timestamp,
        )
        self._order_book.update(updated)
        self._publish(order, updated, timestamp, evaluation.fill_price)

        if (
            updated.request.time_in_force is TimeInForce.IOC
            and not updated.is_terminal
        ):
            return self._cancel_and_publish(updated, timestamp)

        return updated

    def process_open_orders(
        self,
        market_prices: Mapping[str, Decimal],
        *,
        available_quantities: Mapping[str, Decimal] | None = None,
        at: datetime | None = None,
    ) -> tuple[PaperOrder, ...]:
        """Process every open order for which a symbol price is available."""

        normalized_prices = {
            str(symbol).strip().upper(): price
            for symbol, price in market_prices.items()
        }
        normalized_quantities = {
            str(symbol).strip().upper(): quantity
            for symbol, quantity in (available_quantities or {}).items()
        }

        processed: list[PaperOrder] = []
        for order in self._order_book.open_orders():
            market_price = normalized_prices.get(order.symbol)
            if market_price is None:
                continue
            processed.append(
                self.process_order(
                    order.order_id,
                    market_price=market_price,
                    available_quantity=normalized_quantities.get(order.symbol),
                    at=at,
                )
            )
        return tuple(processed)

    def _cancel_and_publish(
        self,
        order: PaperOrder,
        timestamp: datetime,
    ) -> PaperOrder:
        cancelled = cancel_order(order, at=timestamp)
        self._order_book.update(cancelled)
        self._publish(order, cancelled, timestamp, None)
        return cancelled

    def _publish(
        self,
        previous: PaperOrder,
        current: PaperOrder,
        timestamp: datetime,
        fill_price: Decimal | None,
    ) -> None:
        event = OrderLifecycleEvent(
            order_id=current.order_id,
            previous_status=previous.status,
            current_status=current.status,
            occurred_at=timestamp,
            filled_quantity=current.filled_quantity,
            remaining_quantity=current.remaining_quantity,
            fill_price=fill_price,
        )
        for listener in self._listeners:
            listener(event)


def evaluate_order(order: PaperOrder, market_price: Decimal) -> OrderEvaluation:
    """Evaluate price eligibility without mutating the order."""

    if not isinstance(order, PaperOrder):
        raise TypeError("order must be PaperOrder")
    if market_price <= ZERO:
        raise ValueError("market_price must be positive")
    if order.is_terminal:
        return OrderEvaluation(False, None, "order is terminal")

    request = order.request
    if request.order_type is OrderType.MARKET:
        return OrderEvaluation(True, market_price, "market order")

    if request.order_type is OrderType.LIMIT:
        assert request.limit_price is not None
        executable = (
            market_price <= request.limit_price
            if request.side is OrderSide.BUY
            else market_price >= request.limit_price
        )
        return OrderEvaluation(
            executable,
            market_price if executable else None,
            "limit price reached" if executable else "limit price not reached",
        )

    if request.order_type is OrderType.STOP:
        assert request.stop_price is not None
        executable = (
            market_price >= request.stop_price
            if request.side is OrderSide.BUY
            else market_price <= request.stop_price
        )
        return OrderEvaluation(
            executable,
            market_price if executable else None,
            "stop triggered" if executable else "stop not triggered",
        )

    assert request.order_type is OrderType.STOP_LIMIT
    assert request.stop_price is not None
    assert request.limit_price is not None
    stop_triggered = (
        market_price >= request.stop_price
        if request.side is OrderSide.BUY
        else market_price <= request.stop_price
    )
    limit_satisfied = (
        market_price <= request.limit_price
        if request.side is OrderSide.BUY
        else market_price >= request.limit_price
    )
    executable = stop_triggered and limit_satisfied
    return OrderEvaluation(
        executable,
        market_price if executable else None,
        "stop-limit executable" if executable else "stop-limit not executable",
    )


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("lifecycle timestamps must be timezone-aware")


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "OrderEvaluation",
    "OrderLifecycleEvent",
    "OrderLifecycleListener",
    "PaperOrderLifecycleCoordinator",
    "evaluate_order",
]
