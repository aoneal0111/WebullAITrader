"""Stable public operations over the existing paper-order lifecycle."""

from datetime import datetime
from decimal import Decimal

from app.momentum_scanner import AssetClass
from app.paper_trading.fill_models import Fill
from app.paper_trading.order_book import PaperOrderBook
from app.paper_trading.order_models import (
    OrderRequest,
    OrderSide,
    OrderType,
    PaperOrder,
    TimeInForce,
)
from app.paper_trading.orders import (
    OrderValidationError,
    accept_order,
    apply_fill,
    create_order,
    expire_order,
    reject_order,
)


def _enum_value(enum_type, value: object, field_name: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise OrderValidationError(f"{field_name} is invalid") from None


def create_fill(
    *,
    fill_id: str,
    order_id: str,
    quantity: Decimal,
    price: Decimal,
    occurred_at: datetime,
    commission: Decimal = Decimal("0"),
    slippage: Decimal = Decimal("0"),
    venue: str | None = None,
    liquidity_flag: str | None = None,
) -> Fill:
    return Fill(
        fill_id=fill_id,
        order_id=order_id,
        quantity=quantity,
        price=price,
        timestamp=occurred_at,
        commission=commission,
        slippage=slippage,
        venue=venue,
        liquidity_flag=liquidity_flag,
    )


def create_submission_order(
    *,
    order_id: str,
    occurred_at: datetime,
    symbol: str,
    asset_class: str,
    side: str | OrderSide,
    order_type: str | OrderType,
    quantity: Decimal,
    time_in_force: str | TimeInForce,
    limit_price: Decimal | None = None,
    stop_price: Decimal | None = None,
    client_order_id: str | None = None,
) -> PaperOrder:
    if (
        not isinstance(order_id, str)
        or not order_id.strip()
        or order_id != order_id.strip()
    ):
        raise OrderValidationError(
            "order_id must be a nonblank stripped string"
        )
    request = OrderRequest(
        symbol=symbol,
        asset_class=_enum_value(AssetClass, asset_class, "asset_class"),
        side=_enum_value(OrderSide, side, "side"),
        order_type=_enum_value(OrderType, order_type, "order_type"),
        quantity=quantity,
        time_in_force=_enum_value(
            TimeInForce,
            time_in_force,
            "time_in_force",
        ),
        limit_price=limit_price,
        stop_price=stop_price,
        client_order_id=client_order_id,
    )
    return create_order(
        request,
        order_id_factory=lambda: order_id,
        clock=lambda: occurred_at,
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


def reject(
    book: PaperOrderBook,
    order: PaperOrder,
    reason: str,
    *,
    at: datetime,
) -> PaperOrder:
    return book.update(reject_order(order, reason, at=at))


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
    "create_fill",
    "create_submission_order",
    "submit",
    "update",
    "cancel",
    "accept",
    "reject",
    "expire",
    "record_fill",
    "expire_day_orders",
)
