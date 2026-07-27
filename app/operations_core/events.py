from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime, timezone
from uuid import UUID, uuid4


def utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationsEvent:
    """Base class for immutable Operations Center business events."""

    occurred_at: datetime = field(default_factory=utc_now)
    event_id: UUID = field(default_factory=uuid4)
    source: str = "operations"

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")

        if not self.source.strip():
            raise ValueError("source must not be empty")


@dataclass(frozen=True, slots=True)
class OperationsOrder:
    """Backend-neutral immutable order state for Operations Center consumers."""

    order_id: str
    symbol: str
    side: str
    quantity: str
    status: str
    updated_at: datetime

    def __post_init__(self) -> None:
        for field_name in (
            "order_id",
            "symbol",
            "side",
            "quantity",
            "status",
        ):
            value = getattr(self, field_name)

            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be empty")

            if value != value.strip():
                raise ValueError(f"{field_name} must be stripped")

        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")


@dataclass(frozen=True, slots=True, kw_only=True)
class OrdersUpdated(OperationsEvent):
    """Replace the Operations Center order slice with an immutable snapshot."""

    orders: tuple[OperationsOrder, ...] = ()

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)

        if not isinstance(self.orders, tuple):
            raise TypeError("orders must be an immutable tuple")

        if any(
            not isinstance(order, OperationsOrder)
            for order in self.orders
        ):
            raise TypeError(
                "orders must contain only OperationsOrder instances"
            )

@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeStarting(OperationsEvent):
    environment: str = "PAPER"


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeStarted(OperationsEvent):
    environment: str = "PAPER"
    active_model: str = "Not loaded"


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeCycleCompleted(OperationsEvent):
    cycle_count: int

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)

        if self.cycle_count < 0:
            raise ValueError("cycle_count must be nonnegative")


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperRuntimeSnapshot:
    cycle: int
    timestamp: datetime
    session_id: str
    symbols: tuple[str, ...]

    decisions_processed: int
    orders_attempted: int
    orders_filled: int
    orders_rejected: int
    orders_not_filled: int
    decisions_skipped: int

    winning_fills: int
    losing_fills: int
    breakeven_fills: int

    realized_pnl: Decimal
    unrealized_pnl: Decimal
    current_equity: Decimal
    peak_equity: Decimal
    current_drawdown: Decimal

    win_rate: Decimal
    total_return: Decimal
    maximum_drawdown: Decimal

    def __post_init__(self) -> None:
        if (
            isinstance(self.cycle, bool)
            or not isinstance(self.cycle, int)
            or self.cycle < 1
        ):
            raise ValueError(
                "paper runtime cycle must be positive"
            )

        if (
            not isinstance(self.timestamp, datetime)
            or self.timestamp.tzinfo is None
        ):
            raise ValueError(
                "paper runtime timestamp must be timezone-aware"
            )

        session_id = self.session_id.strip()

        if not session_id:
            raise ValueError(
                "paper runtime session ID is required"
            )

        object.__setattr__(
            self,
            "session_id",
            session_id,
        )

        if not isinstance(self.symbols, tuple):
            raise TypeError(
                "paper runtime symbols must be a tuple"
            )

        if any(
            not isinstance(symbol, str)
            for symbol in self.symbols
        ):
            raise TypeError(
                "paper runtime symbols must contain strings"
            )

        symbols = tuple(
            symbol.strip().upper()
            for symbol in self.symbols
        )

        if any(not symbol for symbol in symbols):
            raise ValueError(
                "paper runtime symbols cannot be blank"
            )

        if len(set(symbols)) != len(symbols):
            raise ValueError(
                "paper runtime symbols must be unique"
            )

        object.__setattr__(
            self,
            "symbols",
            symbols,
        )

        counts = (
            self.decisions_processed,
            self.orders_attempted,
            self.orders_filled,
            self.orders_rejected,
            self.orders_not_filled,
            self.decisions_skipped,
            self.winning_fills,
            self.losing_fills,
            self.breakeven_fills,
        )

        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in counts
        ):
            raise ValueError(
                "paper runtime counts must be "
                "nonnegative integers"
            )

        financial_values = (
            self.realized_pnl,
            self.unrealized_pnl,
            self.current_equity,
            self.peak_equity,
            self.current_drawdown,
            self.win_rate,
            self.total_return,
            self.maximum_drawdown,
        )

        if any(
            not isinstance(value, Decimal)
            or not value.is_finite()
            for value in financial_values
        ):
            raise ValueError(
                "paper runtime financial values must be "
                "finite Decimals"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperRuntimeUpdated(OperationsEvent):
    snapshot: PaperRuntimeSnapshot

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)

        if not isinstance(
            self.snapshot,
            PaperRuntimeSnapshot,
        ):
            raise TypeError(
                "snapshot must be a PaperRuntimeSnapshot"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeStopping(OperationsEvent):
    reason: str = "Operator requested shutdown"


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeStopped(OperationsEvent):
    reason: str = "Runtime stopped cleanly"
    cycles_completed: int = 0

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)

        if self.cycles_completed < 0:
            raise ValueError("cycles_completed must be nonnegative")


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeFailed(OperationsEvent):
    error_message: str

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)

        if not self.error_message.strip():
            raise ValueError("error_message must not be empty")
