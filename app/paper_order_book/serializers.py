"""Deterministic serializers for Paper Order Book application models."""

from app.paper_order_book.exceptions import PaperOrderBookSerializationError
from app.paper_order_book.models import (
    PaperOrderBookCommand,
    PaperOrderBookCriteriaResult,
    PaperOrderBookIdentity,
    PaperOrderBookRequest,
    PaperOrderBookResult,
    PaperOrderBookObservation,
    PaperOrderBookSummary,
)
from app.paper_order_book.policies import PaperOrderBookPolicy
from app.paper_trading.order_book_api import (
    OrderBookFill,
    OrderBookOrderRequest,
    OrderBookPaperOrder,
    PaperOrderBook,
)
from app.paper_trading.order_book_api import serializers as lifecycle_serializers


def _require(value: object, expected: type):
    if not isinstance(value, expected):
        raise PaperOrderBookSerializationError(
            f"value must be {expected.__name__}"
        )
    return value


def serialize_identity(value: PaperOrderBookIdentity) -> dict[str, object]:
    value = _require(value, PaperOrderBookIdentity)
    return {"order_book_id": value.order_book_id}


def serialize_snapshot(value: PaperOrderBookObservation) -> dict[str, object]:
    value = _require(value, PaperOrderBookObservation)
    return {
        "identity": serialize_identity(value.identity),
        "order_book": lifecycle_serializers.serialize_order_book(
            value.order_book
        ),
        "captured_at": value.captured_at.isoformat(),
    }


def _serialize_payload(value: object) -> dict[str, object]:
    if isinstance(value, PaperOrderBook):
        return {
            "type": "order_book",
            "value": lifecycle_serializers.serialize_order_book(value),
        }
    if isinstance(value, OrderBookPaperOrder):
        return {
            "type": "order",
            "value": lifecycle_serializers.serialize_order_book_order(value),
        }
    if isinstance(value, OrderBookFill):
        return {
            "type": "fill",
            "value": lifecycle_serializers.serialize_order_book_fill(value),
        }
    if isinstance(value, OrderBookOrderRequest):
        return {
            "type": "order_request",
            "value": lifecycle_serializers.serialize_order_book_request(value),
        }
    raise PaperOrderBookSerializationError(
        "command payload must be a public order-book lifecycle contract"
    )


def serialize_command(value: PaperOrderBookCommand) -> dict[str, object]:
    value = _require(value, PaperOrderBookCommand)
    return {
        "command_id": value.command_id,
        "command_type": value.command_type,
        "payload": _serialize_payload(value.payload),
        "occurred_at": value.occurred_at.isoformat(),
    }


def serialize_policy(value: PaperOrderBookPolicy) -> dict[str, object]:
    value = _require(value, PaperOrderBookPolicy)
    return {
        "reject_duplicate_command_ids": value.reject_duplicate_command_ids,
        "reject_non_monotonic_timestamps": (
            value.reject_non_monotonic_timestamps
        ),
    }


def serialize_request(value: PaperOrderBookRequest) -> dict[str, object]:
    value = _require(value, PaperOrderBookRequest)
    return {
        "identity": serialize_identity(value.identity),
        "snapshot": serialize_snapshot(value.snapshot),
        "commands": [serialize_command(item) for item in value.commands],
        "requested_at": value.requested_at.isoformat(),
        "completed_at": value.completed_at.isoformat(),
        "policy": serialize_policy(value.policy),
    }


def serialize_criteria(
    value: PaperOrderBookCriteriaResult,
) -> dict[str, object]:
    value = _require(value, PaperOrderBookCriteriaResult)
    return {"accepted": value.accepted, "errors": list(value.errors)}


def serialize_summary(value: PaperOrderBookSummary) -> dict[str, object]:
    value = _require(value, PaperOrderBookSummary)
    return {
        "initial_orders": value.initial_orders,
        "command_count": value.command_count,
    }


def serialize_result(value: PaperOrderBookResult) -> dict[str, object]:
    value = _require(value, PaperOrderBookResult)
    return {
        "identity": serialize_identity(value.identity),
        "snapshot": serialize_snapshot(value.snapshot),
        "commands": [serialize_command(item) for item in value.commands],
        "summary": serialize_summary(value.summary),
        "criteria": serialize_criteria(value.criteria),
        "requested_at": value.requested_at.isoformat(),
        "completed_at": value.completed_at.isoformat(),
        "errors": list(value.errors),
    }


__all__ = (
    "serialize_identity",
    "serialize_snapshot",
    "serialize_command",
    "serialize_policy",
    "serialize_request",
    "serialize_criteria",
    "serialize_summary",
    "serialize_result",
)
