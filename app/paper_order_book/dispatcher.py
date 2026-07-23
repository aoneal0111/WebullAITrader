"""Internal routing for public paper-order lifecycle operations."""

import app.paper_trading.order_book_api as lifecycle_api

from app.paper_order_book.exceptions import PaperOrderBookValidationError
from app.paper_order_book.models import (
    PaperOrderBookCommand,
    PaperOrderBookRejection,
)


def dispatch_command(
    order_book: lifecycle_api.PaperOrderBook,
    command: PaperOrderBookCommand,
) -> None:
    payload = command.payload
    if command.command_type == "submit" and isinstance(
        payload, lifecycle_api.OrderBookPaperOrder
    ):
        lifecycle_api.submit(order_book, payload)
        return
    if command.command_type == "update" and isinstance(
        payload, lifecycle_api.OrderBookPaperOrder
    ):
        lifecycle_api.update(order_book, payload)
        return
    if command.command_type == "cancel" and isinstance(
        payload, lifecycle_api.OrderBookPaperOrder
    ):
        lifecycle_api.cancel(
            order_book,
            payload,
            at=command.occurred_at,
        )
        return
    if command.command_type == "accept" and isinstance(
        payload, lifecycle_api.OrderBookPaperOrder
    ):
        lifecycle_api.accept(
            order_book,
            payload,
            at=command.occurred_at,
        )
        return
    if command.command_type == "reject" and isinstance(
        payload, PaperOrderBookRejection
    ):
        lifecycle_api.reject(
            order_book,
            payload.order,
            payload.reason,
            at=command.occurred_at,
        )
        return
    if command.command_type == "expire" and isinstance(
        payload, lifecycle_api.OrderBookPaperOrder
    ):
        lifecycle_api.expire(
            order_book,
            payload,
            at=command.occurred_at,
        )
        return
    if command.command_type == "expire_day_orders" and payload is order_book:
        lifecycle_api.expire_day_orders(
            order_book,
            at=command.occurred_at,
        )
        return
    if command.command_type == "apply_fill" and isinstance(
        payload, lifecycle_api.OrderBookFill
    ):
        lifecycle_api.record_fill(order_book, payload)
        return
    raise PaperOrderBookValidationError(
        f"unsupported command payload for command_type: "
        f"{command.command_type}"
    )


__all__ = ()
