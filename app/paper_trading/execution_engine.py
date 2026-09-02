from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Callable

from app.paper_trading.fill_models import Fill
from app.paper_trading.matching_engine import (
    MarketQuote,
    MatchResult,
    match_order,
)
from app.paper_trading.order_book import PaperOrderBook
from app.paper_trading.order_models import (
    OrderStatus,
    PaperOrder,
)
from app.paper_trading.orders import apply_fill


class ExecutionEngineError(RuntimeError):
    """Raised when paper execution orchestration cannot proceed safely."""


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """Immutable result of one paper-execution operation."""

    order: PaperOrder
    match_result: MatchResult | None
    fills: tuple[Fill, ...]
    message: str

    @property
    def matched(self) -> bool:
        return (
            self.match_result is not None
            and self.match_result.matched
        )


class PaperExecutionEngine:
    """Coordinates immutable paper-order execution.

    The engine delegates:

    - storage and lifecycle indexing to PaperOrderBook
    - pricing and eligibility to match_order
    - immutable fill transitions to apply_fill

    It does not update portfolios, calculate prices, call brokers, or perform
    network operations.
    """

    def __init__(
        self,
        order_book: PaperOrderBook | None = None,
        *,
        fill_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._order_book = (
            order_book
            if order_book is not None
            else PaperOrderBook()
        )
        self._fill_id_factory = fill_id_factory
        self._lock = RLock()

    @property
    def order_book(self) -> PaperOrderBook:
        return self._order_book

    def submit(self, order: PaperOrder) -> ExecutionReport:
        """Store an accepted immutable order in the order book."""

        if order.status is not OrderStatus.ACCEPTED:
            raise ExecutionEngineError(
                "only accepted orders can be submitted"
            )

        with self._lock:
            stored = self._order_book.submit(order)

        return ExecutionReport(
            order=stored,
            match_result=None,
            fills=(),
            message=f"Order {stored.order_id} submitted",
        )

    def process_quote(
        self,
        quote: MarketQuote,
        *,
        before_update: Callable[[ExecutionReport], None] | None = None,
    ) -> tuple[ExecutionReport, ...]:
        """Process a quote, optionally durably recording before each mutation."""

        if before_update is not None and not callable(before_update):
            raise TypeError("before_update must be callable or None")

        with self._lock:
            reports: list[ExecutionReport] = []

            for order in self._order_book.open_orders_for_symbol(
                quote.symbol
            ):
                result = match_order(order, quote)

                if not result.matched:
                    reports.append(
                        ExecutionReport(
                            order=order,
                            match_result=result,
                            fills=(),
                            message=(
                                f"Order {order.order_id} was not filled: "
                                f"{result.reason}"
                            ),
                        ),
                    )
                    continue

                if result.execution_price is None:
                    raise ExecutionEngineError(
                        "matched result did not contain an execution price"
                    )

                updated = apply_fill(
                    order,
                    result.filled_quantity,
                    result.execution_price,
                    at=result.timestamp,
                    slippage=result.slippage,
                    liquidity_flag=result.liquidity_flag,
                    fill_id_factory=self._fill_id_factory,
                )

                new_fill = updated.fills[-1]
                report = ExecutionReport(
                    order=updated,
                    match_result=result,
                    fills=(new_fill,),
                    message=_fill_message(updated, new_fill),
                )
                if before_update is not None:
                    before_update(report)
                self._order_book.update(updated)
                reports.append(report)

            return tuple(reports)

    def cancel(
        self,
        order_id: str,
        *,
        at: datetime | None = None,
    ) -> ExecutionReport:
        """Cancel an open order through the authoritative order book."""

        with self._lock:
            cancelled = self._order_book.cancel(
                order_id,
                at=at,
            )

        return ExecutionReport(
            order=cancelled,
            match_result=None,
            fills=(),
            message=f"Order {cancelled.order_id} cancelled",
        )

    def expire_day_orders(
        self,
        *,
        at: datetime | None = None,
    ) -> tuple[ExecutionReport, ...]:
        """Expire all currently open DAY orders."""

        with self._lock:
            expired = self._order_book.expire_day_orders(at=at)

        return tuple(
            ExecutionReport(
                order=order,
                match_result=None,
                fills=(),
                message=f"Order {order.order_id} expired",
            )
            for order in expired
        )


def _fill_message(
    order: PaperOrder,
    fill: Fill,
) -> str:
    if order.status is OrderStatus.FILLED:
        state = "filled"
    else:
        state = "partially filled"

    return (
        f"Order {order.order_id} {state}: "
        f"{fill.quantity} at {fill.price}"
    )
