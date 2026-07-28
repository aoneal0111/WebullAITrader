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



@dataclass(frozen=True, slots=True)
class OperationsPosition:
    """Backend-neutral immutable position state for Operations Center consumers."""

    account_id: str
    symbol: str
    asset_type: str
    quantity: str
    average_cost: str
    market_value: str
    unrealized_gain_loss: str
    realized_gain_loss: str | None
    currency: str
    updated_at: datetime

    def __post_init__(self) -> None:
        required_text_fields = (
            "account_id",
            "symbol",
            "asset_type",
            "quantity",
            "average_cost",
            "market_value",
            "unrealized_gain_loss",
            "currency",
        )

        for field_name in required_text_fields:
            value = getattr(self, field_name)

            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be empty")

            if value != value.strip():
                raise ValueError(f"{field_name} must be stripped")

        if self.realized_gain_loss is not None:
            if (
                not isinstance(self.realized_gain_loss, str)
                or not self.realized_gain_loss.strip()
            ):
                raise ValueError(
                    "realized_gain_loss must be None or non-empty text"
                )

            if self.realized_gain_loss != self.realized_gain_loss.strip():
                raise ValueError("realized_gain_loss must be stripped")

        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class OperationsDecision:
    """Backend-neutral immutable strategy decision for read-model consumers."""

    symbol: str
    action: str
    confidence: int
    score: Decimal
    reasons: tuple[str, ...]
    source_action: str
    position_quantity: Decimal
    strategy_version: str
    decided_at: datetime

    def __post_init__(self) -> None:
        for field_name in (
            "symbol",
            "action",
            "source_action",
            "strategy_version",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be empty")
            if value != value.strip():
                raise ValueError(f"{field_name} must be stripped")

        if self.symbol != self.symbol.upper():
            raise ValueError("symbol must be uppercase")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, int)
            or not 0 <= self.confidence <= 100
        ):
            raise ValueError("confidence must be an integer between 0 and 100")
        for field_name in ("score", "position_quantity"):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{field_name} must be a finite Decimal")
        if not isinstance(self.reasons, tuple):
            raise TypeError("reasons must be an immutable tuple")
        if any(
            not isinstance(reason, str)
            or not reason.strip()
            or reason != reason.strip()
            for reason in self.reasons
        ):
            raise ValueError("reasons must contain stripped non-empty strings")
        if (
            not isinstance(self.decided_at, datetime)
            or self.decided_at.tzinfo is None
        ):
            raise ValueError("decided_at must be timezone-aware")


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionsUpdated(OperationsEvent):
    """Replace the current strategy-decision slice for one completed cycle."""

    cycle: int
    decisions: tuple[OperationsDecision, ...] = ()

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)
        if (
            isinstance(self.cycle, bool)
            or not isinstance(self.cycle, int)
            or self.cycle < 1
        ):
            raise ValueError("cycle must be a positive integer")
        if not isinstance(self.decisions, tuple):
            raise TypeError("decisions must be an immutable tuple")
        if any(
            not isinstance(decision, OperationsDecision)
            for decision in self.decisions
        ):
            raise TypeError(
                "decisions must contain only OperationsDecision instances"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class TradeLifecycleUpdated(OperationsEvent):
    """Broker-neutral lifecycle fact for symbol-scoped trade reconstruction."""

    symbol: str
    phase: str
    title: str
    description: str
    order_id: str | None = None
    position_id: str | None = None
    cycle: int | None = None
    realized_pnl: Decimal | None = None

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)
        for field_name in ("symbol", "phase", "title", "description"):
            value = getattr(self, field_name)
            if (
                not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
            ):
                raise ValueError(
                    f"{field_name} must be stripped non-empty text"
                )
        if self.symbol != self.symbol.upper():
            raise ValueError("symbol must be uppercase")
        if self.phase != self.phase.upper():
            raise ValueError("phase must be uppercase")
        for field_name in ("order_id", "position_id"):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
            ):
                raise ValueError(
                    f"{field_name} must be stripped non-empty text or None"
                )
        if self.cycle is not None and (
            isinstance(self.cycle, bool)
            or not isinstance(self.cycle, int)
            or self.cycle < 0
        ):
            raise ValueError("cycle must be a nonnegative integer or None")
        if self.realized_pnl is not None and (
            not isinstance(self.realized_pnl, Decimal)
            or not self.realized_pnl.is_finite()
        ):
            raise ValueError("realized_pnl must be a finite Decimal or None")


@dataclass(frozen=True, slots=True, kw_only=True)
class OperatorSelectionEvent(OperationsEvent):
    """Base type for immutable, broker-neutral operator selections."""


@dataclass(frozen=True, slots=True, kw_only=True)
class OperatorSymbolSelected(OperatorSelectionEvent):
    symbol: str
    selection_source: str = "NONE"
    selection_id: str | None = None

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)
        _validate_operator_symbol(self.symbol)
        allowed_sources = {"POSITION", "ORDER", "NONE"}
        if self.selection_source not in allowed_sources:
            raise ValueError(
                "selection_source must be POSITION, ORDER, or NONE"
            )
        _validate_optional_selection_id(self.selection_id)
        if (
            self.selection_source == "ORDER"
            and self.selection_id is None
        ):
            raise ValueError("ORDER selection requires selection_id")


@dataclass(frozen=True, slots=True, kw_only=True)
class OperatorTradeSelected(OperatorSelectionEvent):
    symbol: str

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)
        _validate_operator_symbol(self.symbol)


@dataclass(frozen=True, slots=True, kw_only=True)
class OperatorDecisionSelected(OperatorSelectionEvent):
    symbol: str
    decision_id: str

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)
        _validate_operator_symbol(self.symbol)
        _validate_selection_id(self.decision_id, "decision_id")


@dataclass(frozen=True, slots=True, kw_only=True)
class OperatorTimelineSelected(OperatorSelectionEvent):
    timeline_entry_id: str
    symbol: str | None = None

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)
        _validate_selection_id(
            self.timeline_entry_id,
            "timeline_entry_id",
        )
        if self.symbol is not None:
            _validate_operator_symbol(self.symbol)


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperOrderLifecycleUpdated(OperationsEvent):
    """One deterministic paper-order lifecycle transition."""

    order_id: str
    previous_status: str
    current_status: str
    filled_quantity: Decimal
    remaining_quantity: Decimal
    fill_price: Decimal | None = None
    symbol: str | None = None

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)

        for field_name in (
            "order_id",
            "previous_status",
            "current_status",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be empty")
            if value != value.strip():
                raise ValueError(f"{field_name} must be stripped")

        if self.filled_quantity < Decimal("0"):
            raise ValueError("filled_quantity must be nonnegative")
        if self.remaining_quantity < Decimal("0"):
            raise ValueError("remaining_quantity must be nonnegative")
        if self.fill_price is not None and self.fill_price <= Decimal("0"):
            raise ValueError("fill_price must be positive when provided")
        if self.symbol is not None:
            if (
                not isinstance(self.symbol, str)
                or not self.symbol.strip()
                or self.symbol != self.symbol.strip()
            ):
                raise ValueError(
                    "symbol must be stripped non-empty text or None"
                )
            if self.symbol != self.symbol.upper():
                raise ValueError("symbol must be uppercase")


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
class PositionsUpdated(OperationsEvent):
    """Replace the Operations Center position slice with an immutable snapshot."""

    positions: tuple[OperationsPosition, ...] = ()

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)

        if not isinstance(self.positions, tuple):
            raise TypeError("positions must be an immutable tuple")

        if any(
            not isinstance(position, OperationsPosition)
            for position in self.positions
        ):
            raise TypeError(
                "positions must contain only OperationsPosition instances"
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


def _validate_operator_symbol(symbol: str) -> None:
    if (
        not isinstance(symbol, str)
        or not symbol.strip()
        or symbol != symbol.strip()
    ):
        raise ValueError("symbol must be stripped non-empty text")
    if symbol != symbol.upper():
        raise ValueError("symbol must be uppercase")


def _validate_selection_id(value: str, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
    ):
        raise ValueError(
            f"{field_name} must be stripped non-empty text"
        )


def _validate_optional_selection_id(value: str | None) -> None:
    if value is not None:
        _validate_selection_id(value, "selection_id")
