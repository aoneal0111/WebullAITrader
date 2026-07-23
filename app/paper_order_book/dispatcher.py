"""Internal routing for public paper-order lifecycle operations."""

import app.paper_trading.order_book_api as lifecycle_api

from app.paper_order_book.command_contracts import (
    ACCEPT,
    APPLY_FILL,
    CANCEL,
    COMMAND_PAYLOAD_TYPES,
    EXPIRE,
    EXPIRE_DAY_ORDERS,
    REJECT,
    SUBMIT,
    UPDATE,
)
from app.paper_order_book.exceptions import PaperOrderBookValidationError
from app.paper_order_book.models import PaperOrderBookCommand


def dispatch_command(
    order_book: lifecycle_api.PaperOrderBook,
    command: PaperOrderBookCommand,
) -> None:
    payload = command.payload
    expected_payload = COMMAND_PAYLOAD_TYPES.get(command.command_type)
    if expected_payload is None or not isinstance(payload, expected_payload):
        raise PaperOrderBookValidationError(
            f"unsupported command payload for command_type: "
            f"{command.command_type}"
        )

    if command.command_type == SUBMIT:
        lifecycle_api.submit(order_book, payload)
        return
    if command.command_type == UPDATE:
        lifecycle_api.update(order_book, payload)
        return
    if command.command_type == CANCEL:
        lifecycle_api.cancel(
            order_book,
            payload,
            at=command.occurred_at,
        )
        return
    if command.command_type == ACCEPT:
        lifecycle_api.accept(
            order_book,
            payload,
            at=command.occurred_at,
        )
        return
    if command.command_type == REJECT:
        lifecycle_api.reject(
            order_book,
            payload.order,
            payload.reason,
            at=command.occurred_at,
        )
        return
    if command.command_type == EXPIRE:
        lifecycle_api.expire(
            order_book,
            payload,
            at=command.occurred_at,
        )
        return
    if command.command_type == EXPIRE_DAY_ORDERS and payload is order_book:
        lifecycle_api.expire_day_orders(
            order_book,
            at=command.occurred_at,
        )
        return
    if command.command_type == APPLY_FILL:
        lifecycle_api.record_fill(order_book, payload)
        return
    raise PaperOrderBookValidationError(
        f"unsupported command payload for command_type: "
        f"{command.command_type}"
    )


__all__ = ()
