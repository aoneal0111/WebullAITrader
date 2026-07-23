"""Stable public operations over the existing paper-order lifecycle."""

from datetime import datetime

from app.paper_trading.fill_models import Fill
from app.paper_trading.order_book import PaperOrderBook
from app.paper_trading.order_models import PaperOrder
from app.paper_trading.orders import (
    accept_order,
    apply_fill,
    expire_order,
)


def submit(book: PaperOrderBook, order: PaperOrder) -> PaperOrder:
    return book.submit(order)


def update(book: PaperOrderBook, order: PaperOrder) -> PaperOrder:
    return book.update(order)


def cancel(
    book: PaperOrderBook,
    order: PaperOrder,
    *,
    at: datetime,
) -> PaperOrder:
    return book.cancel(order.order_id, at=at)


def accept(
    book: PaperOrderBook,
    order: PaperOrder,
    *,
    at: datetime,
) -> PaperOrder:
    return book.update(accept_order(order, at=at))


def expire(
    book: PaperOrderBook,
    order: PaperOrder,
    *,
    at: datetime,
) -> PaperOrder:
    return book.update(expire_order(order, at=at))


def record_fill(book: PaperOrderBook, fill: Fill) -> PaperOrder:
    current = book.get(fill.order_id)
    updated = apply_fill(
        current,
        fill.quantity,
        fill.price,
        at=fill.timestamp,
        commission=fill.commission,
        slippage=fill.slippage,
        venue=fill.venue,
        liquidity_flag=fill.liquidity_flag,
        fill_id_factory=lambda: fill.fill_id,
    )
    return book.update(updated)


def expire_day_orders(
    book: PaperOrderBook,
    *,
    at: datetime,
) -> tuple[PaperOrder, ...]:
    return book.expire_day_orders(at=at)


__all__ = (
    "submit",
    "update",
    "cancel",
    "accept",
    "expire",
    "record_fill",
    "expire_day_orders",
)
