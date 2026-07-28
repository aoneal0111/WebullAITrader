from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class TradeLifecyclePhase(StrEnum):
    SCANNED = "SCANNED"
    EVIDENCE = "EVIDENCE"
    COMMITTEE = "COMMITTEE"
    DECISION = "DECISION"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_ACCEPTED = "ORDER_ACCEPTED"
    PARTIAL_FILL = "PARTIAL_FILL"
    FILLED = "FILLED"
    POSITION_OPEN = "POSITION_OPEN"
    RISK_UPDATE = "RISK_UPDATE"
    STOP_UPDATED = "STOP_UPDATED"
    TARGET_UPDATED = "TARGET_UPDATED"
    POSITION_CLOSE = "POSITION_CLOSE"
    EXIT = "EXIT"
    ERROR = "ERROR"


class TradeLifecycleStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class TradeLifecycleEntry:
    timestamp: datetime
    phase: TradeLifecyclePhase
    title: str
    description: str
    symbol: str
    order_id: str | None = None
    position_id: str | None = None
    cycle: int | None = None

    def __post_init__(self) -> None:
        _validate_timestamp(self.timestamp, "timestamp")
        if not isinstance(self.phase, TradeLifecyclePhase):
            raise TypeError("phase must be a TradeLifecyclePhase")
        for field_name in ("title", "description"):
            _validate_text(getattr(self, field_name), field_name)
        _validate_symbol(self.symbol)
        for field_name in ("order_id", "position_id"):
            value = getattr(self, field_name)
            if value is not None:
                _validate_text(value, field_name)
        if self.cycle is not None and (
            isinstance(self.cycle, bool)
            or not isinstance(self.cycle, int)
            or self.cycle < 0
        ):
            raise ValueError("cycle must be a nonnegative integer or None")


@dataclass(frozen=True, slots=True)
class TradeLifecycle:
    symbol: str
    entries: tuple[TradeLifecycleEntry, ...]
    status: TradeLifecycleStatus
    opened_at: datetime | None
    closed_at: datetime | None
    realized_pnl: Decimal

    def __post_init__(self) -> None:
        _validate_symbol(self.symbol)
        if not isinstance(self.entries, tuple):
            raise TypeError("entries must be an immutable tuple")
        if any(
            not isinstance(entry, TradeLifecycleEntry)
            for entry in self.entries
        ):
            raise TypeError(
                "entries must contain only TradeLifecycleEntry instances"
            )
        if any(entry.symbol != self.symbol for entry in self.entries):
            raise ValueError("entry symbols must match lifecycle symbol")
        if not isinstance(self.status, TradeLifecycleStatus):
            raise TypeError("status must be a TradeLifecycleStatus")
        _validate_optional_timestamp(self.opened_at, "opened_at")
        _validate_optional_timestamp(self.closed_at, "closed_at")
        if (
            self.opened_at is not None
            and self.closed_at is not None
            and self.closed_at < self.opened_at
        ):
            raise ValueError("closed_at cannot precede opened_at")
        if self.status is TradeLifecycleStatus.OPEN:
            if self.opened_at is None or self.closed_at is not None:
                raise ValueError(
                    "open lifecycle requires opened_at and no closed_at"
                )
        if self.status is TradeLifecycleStatus.CLOSED:
            if self.opened_at is None or self.closed_at is None:
                raise ValueError(
                    "closed lifecycle requires opened_at and closed_at"
                )
        if (
            not isinstance(self.realized_pnl, Decimal)
            or not self.realized_pnl.is_finite()
        ):
            raise ValueError("realized_pnl must be a finite Decimal")


@dataclass(frozen=True, slots=True)
class TradeLifecycleSnapshot:
    lifecycles: tuple[TradeLifecycle, ...] = ()
    selected_symbol: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.lifecycles, tuple):
            raise TypeError("lifecycles must be an immutable tuple")
        if any(
            not isinstance(lifecycle, TradeLifecycle)
            for lifecycle in self.lifecycles
        ):
            raise TypeError(
                "lifecycles must contain only TradeLifecycle instances"
            )
        symbols = tuple(
            lifecycle.symbol
            for lifecycle in self.lifecycles
        )
        if len(set(symbols)) != len(symbols):
            raise ValueError("lifecycle symbols must be unique")
        if self.selected_symbol is not None:
            _validate_symbol(self.selected_symbol)
            if self.selected_symbol not in symbols:
                raise ValueError(
                    "selected_symbol must identify an existing lifecycle"
                )

    @classmethod
    def initial(cls) -> "TradeLifecycleSnapshot":
        return cls()


def _validate_symbol(value: str) -> None:
    _validate_text(value, "symbol")
    if value != value.upper():
        raise ValueError("symbol must be uppercase")


def _validate_text(value: str, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
    ):
        raise ValueError(f"{field_name} must be stripped non-empty text")


def _validate_timestamp(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _validate_optional_timestamp(
    value: datetime | None,
    field_name: str,
) -> None:
    if value is not None:
        _validate_timestamp(value, field_name)
