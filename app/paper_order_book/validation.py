"""Observational validation for Paper Order Book application requests."""

from app.paper_order_book.exceptions import PaperOrderBookValidationError
from app.paper_order_book.models import (
    PaperOrderBookCriteriaResult,
    PaperOrderBookRequest,
)
from app.paper_trading.order_book_api import (
    OrderBookFill,
    OrderBookPaperOrder,
    PaperOrderBook,
)

_COMMAND_PAYLOAD_TYPES = {
    "submit": OrderBookPaperOrder,
    "update": OrderBookPaperOrder,
    "cancel": OrderBookPaperOrder,
    "accept": OrderBookPaperOrder,
    "expire": OrderBookPaperOrder,
    "apply_fill": OrderBookFill,
    "expire_day_orders": PaperOrderBook,
}


def validate_request(request: object) -> PaperOrderBookCriteriaResult:
    if not isinstance(request, PaperOrderBookRequest):
        raise PaperOrderBookValidationError(
            "request must be PaperOrderBookRequest"
        )

    errors: list[str] = []
    if request.identity != request.snapshot.identity:
        errors.append("snapshot identity must match request identity")
    if request.snapshot.captured_at > request.requested_at:
        errors.append("captured_at cannot follow requested_at")
    if request.completed_at < request.requested_at:
        errors.append("completed_at cannot precede requested_at")

    seen: set[str] = set()
    previous_timestamp = request.snapshot.captured_at
    for index, command in enumerate(request.commands):
        if (
            request.policy.reject_duplicate_command_ids
            and command.command_id in seen
        ):
            errors.append(
                f"duplicate command_id at command index {index}: "
                f"{command.command_id}"
            )
        seen.add(command.command_id)

        if command.occurred_at < request.snapshot.captured_at:
            errors.append(
                f"command occurred_at precedes snapshot at command index {index}"
            )
        if command.occurred_at > request.completed_at:
            errors.append(
                f"command occurred_at follows completed_at at command index {index}"
            )
        if (
            request.policy.reject_non_monotonic_timestamps
            and command.occurred_at < previous_timestamp
        ):
            errors.append(
                f"command timestamps are not monotonic at command index {index}"
            )
        previous_timestamp = command.occurred_at

    for index, command in enumerate(request.commands):
        expected_payload = _COMMAND_PAYLOAD_TYPES.get(command.command_type)
        if expected_payload is None:
            errors.append(
                f"unsupported command_type at command index {index}: "
                f"{command.command_type}"
            )
        elif not isinstance(command.payload, expected_payload):
            errors.append(
                f"invalid payload for command_type {command.command_type} "
                f"at command index {index}"
            )

    return PaperOrderBookCriteriaResult(
        accepted=not errors,
        errors=tuple(errors),
    )


__all__ = ("validate_request",)
