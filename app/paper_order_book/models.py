"""Immutable application coordination models for a paper order book."""

from dataclasses import dataclass, field
from datetime import datetime

from app.paper_order_book.exceptions import PaperOrderBookValidationError
from app.paper_order_book.policies import PaperOrderBookPolicy
from app.paper_trading.order_book_api import (
    OrderBookFill,
    OrderBookOrderRequest,
    OrderBookPaperOrder,
    PaperOrderBook,
)

PaperOrderBookCommandPayload = (
    PaperOrderBook
    | OrderBookPaperOrder
    | OrderBookFill
    | OrderBookOrderRequest
)


def _text(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
    ):
        raise PaperOrderBookValidationError(
            f"{name} must be a nonblank stripped string"
        )
    return value


def _timestamp(value: object, name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise PaperOrderBookValidationError(
            f"{name} must be timezone-aware"
        )
    return value


def _errors(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple) or any(
        not isinstance(item, str) or not item.strip()
        for item in value
    ):
        raise PaperOrderBookValidationError(
            "errors must be an immutable tuple of nonblank strings"
        )
    return value


@dataclass(frozen=True, slots=True)
class PaperOrderBookIdentity:
    order_book_id: str

    def __post_init__(self) -> None:
        _text(self.order_book_id, "order_book_id")


@dataclass(frozen=True, slots=True)
class PaperOrderBookObservation:
    identity: PaperOrderBookIdentity
    order_book: PaperOrderBook
    captured_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.identity, PaperOrderBookIdentity):
            raise PaperOrderBookValidationError(
                "snapshot identity must be PaperOrderBookIdentity"
            )
        if not isinstance(self.order_book, PaperOrderBook):
            raise PaperOrderBookValidationError(
                "snapshot order_book must be PaperOrderBook"
            )
        _timestamp(self.captured_at, "captured_at")


@dataclass(frozen=True, slots=True)
class PaperOrderBookCommand:
    command_id: str
    command_type: str
    payload: PaperOrderBookCommandPayload
    occurred_at: datetime

    def __post_init__(self) -> None:
        _text(self.command_id, "command_id")
        _text(self.command_type, "command_type")
        if not isinstance(
            self.payload,
            (
                PaperOrderBook,
                OrderBookPaperOrder,
                OrderBookFill,
                OrderBookOrderRequest,
            ),
        ):
            raise PaperOrderBookValidationError(
                "payload must be a public order-book lifecycle contract"
            )
        _timestamp(self.occurred_at, "occurred_at")


@dataclass(frozen=True, slots=True)
class PaperOrderBookRequest:
    identity: PaperOrderBookIdentity
    snapshot: PaperOrderBookObservation
    commands: tuple[PaperOrderBookCommand, ...]
    requested_at: datetime
    completed_at: datetime
    policy: PaperOrderBookPolicy = field(default_factory=PaperOrderBookPolicy)

    def __post_init__(self) -> None:
        if not isinstance(self.identity, PaperOrderBookIdentity):
            raise PaperOrderBookValidationError(
                "request identity must be PaperOrderBookIdentity"
            )
        if not isinstance(self.snapshot, PaperOrderBookObservation):
            raise PaperOrderBookValidationError(
                "snapshot must be PaperOrderBookObservation"
            )
        if not isinstance(self.commands, tuple) or any(
            not isinstance(command, PaperOrderBookCommand)
            for command in self.commands
        ):
            raise PaperOrderBookValidationError(
                "commands must be an immutable command tuple"
            )
        if not isinstance(self.policy, PaperOrderBookPolicy):
            raise PaperOrderBookValidationError(
                "policy must be PaperOrderBookPolicy"
            )
        _timestamp(self.requested_at, "requested_at")
        _timestamp(self.completed_at, "completed_at")


@dataclass(frozen=True, slots=True)
class PaperOrderBookCriteriaResult:
    accepted: bool
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.accepted, bool):
            raise PaperOrderBookValidationError("accepted must be boolean")
        _errors(self.errors)
        if self.accepted == bool(self.errors):
            raise PaperOrderBookValidationError(
                "accepted and errors are inconsistent"
            )


@dataclass(frozen=True, slots=True)
class PaperOrderBookSummary:
    initial_orders: int
    command_count: int

    def __post_init__(self) -> None:
        for name in ("initial_orders", "command_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise PaperOrderBookValidationError(
                    "summary counts must be nonnegative integers"
                )


@dataclass(frozen=True, slots=True)
class PaperOrderBookResult:
    identity: PaperOrderBookIdentity
    snapshot: PaperOrderBookObservation
    commands: tuple[PaperOrderBookCommand, ...]
    summary: PaperOrderBookSummary
    criteria: PaperOrderBookCriteriaResult
    requested_at: datetime
    completed_at: datetime
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, PaperOrderBookIdentity):
            raise PaperOrderBookValidationError(
                "result identity must be PaperOrderBookIdentity"
            )
        if not isinstance(self.snapshot, PaperOrderBookObservation):
            raise PaperOrderBookValidationError(
                "result snapshot must be PaperOrderBookObservation"
            )
        if not isinstance(self.commands, tuple) or any(
            not isinstance(command, PaperOrderBookCommand)
            for command in self.commands
        ):
            raise PaperOrderBookValidationError(
                "result commands must be an immutable command tuple"
            )
        if not isinstance(self.summary, PaperOrderBookSummary):
            raise PaperOrderBookValidationError(
                "summary must be PaperOrderBookSummary"
            )
        if not isinstance(self.criteria, PaperOrderBookCriteriaResult):
            raise PaperOrderBookValidationError(
                "criteria must be PaperOrderBookCriteriaResult"
            )
        _timestamp(self.requested_at, "requested_at")
        _timestamp(self.completed_at, "completed_at")
        _errors(self.errors)
        if self.summary.initial_orders != len(self.snapshot.order_book):
            raise PaperOrderBookValidationError(
                "initial_orders must match the observed snapshot book"
            )
        if self.summary.command_count != len(self.commands):
            raise PaperOrderBookValidationError(
                "command_count must match commands"
            )
        if self.errors != self.criteria.errors:
            raise PaperOrderBookValidationError(
                "result errors must match criteria errors"
            )


__all__ = (
    "PaperOrderBookIdentity",
    "PaperOrderBookObservation",
    "PaperOrderBookCommand",
    "PaperOrderBookRequest",
    "PaperOrderBookCriteriaResult",
    "PaperOrderBookSummary",
    "PaperOrderBookResult",
)
