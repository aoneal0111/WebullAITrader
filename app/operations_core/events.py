from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.account_information.models import BrokerNeutralAccountInformation


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
    market_value: str | None
    unrealized_gain_loss: str | None
    realized_gain_loss: str | None
    currency: str
    updated_at: datetime
    exposure: str | None = None

    def __post_init__(self) -> None:
        required_text_fields = (
            "account_id",
            "symbol",
            "asset_type",
            "quantity",
            "average_cost",
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

        for field_name in (
            "market_value",
            "unrealized_gain_loss",
            "exposure",
        ):
            value = getattr(self, field_name)
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"{field_name} must be None or non-empty text"
                    )
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
class BrokerAccountUpdated(OperationsEvent):
    """Replace the broker account information with an immutable snapshot."""

    account: BrokerNeutralAccountInformation

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)
        if not isinstance(
            self.account,
            BrokerNeutralAccountInformation,
        ):
            raise TypeError(
                "account must be BrokerNeutralAccountInformation"
            )


@dataclass(frozen=True, slots=True)
class OperationsTimelineEntry:
    """Backend-neutral immutable timeline entry for state consumers."""

    timestamp: datetime
    category: str
    severity: str
    source: str
    title: str
    description: str
    related_symbol: str | None = None
    related_order_id: str | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("timeline timestamp must be timezone-aware")
        for field_name in (
            "category",
            "severity",
            "source",
            "title",
            "description",
        ):
            value = getattr(self, field_name)
            if (
                not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
            ):
                raise ValueError(
                    f"timeline {field_name} must be non-empty stripped text"
                )
        for field_name in (
            "related_symbol",
            "related_order_id",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
            ):
                raise ValueError(
                    f"timeline {field_name} must be None or stripped text"
                )


@dataclass(frozen=True, slots=True)
class OperationsDecisionRecord:
    """Backend-neutral immutable trading-decision lifecycle."""

    decision_id: str
    timestamp: datetime
    strategy_id: str
    symbol: str
    action: str
    confidence: int
    reasoning_summary: str
    risk_assessment: str | None
    requested_quantity: str | None
    resulting_order_id: str | None
    execution_outcome: str

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("decision timestamp must be timezone-aware")
        for name in (
            "decision_id",
            "strategy_id",
            "symbol",
            "action",
            "reasoning_summary",
            "execution_outcome",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"decision {name} must be non-empty text")
        if not 0 <= self.confidence <= 100:
            raise ValueError("decision confidence must be between 0 and 100")
        for name in (
            "risk_assessment",
            "requested_quantity",
            "resulting_order_id",
        ):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(
                    f"decision {name} must be None or non-empty text"
                )


@dataclass(frozen=True, slots=True)
class OperationsPortfolioHighlight:
    symbol: str
    value: str

    def __post_init__(self) -> None:
        for field_name in ("symbol", "value"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"portfolio highlight {field_name} is required"
                )


@dataclass(frozen=True, slots=True)
class OperationsPortfolioSummary:
    total_market_value: str | None
    total_cost_basis: str
    realized_pnl: str | None
    unrealized_pnl: str | None
    total_pnl: str | None
    gross_exposure: str | None
    long_exposure: str | None
    short_exposure: str | None
    open_positions: int
    working_orders: int
    winning_positions: int | None
    losing_positions: int | None
    largest_position: OperationsPortfolioHighlight | None
    largest_unrealized_gain: OperationsPortfolioHighlight | None
    largest_unrealized_loss: OperationsPortfolioHighlight | None

    def __post_init__(self) -> None:
        if not isinstance(self.total_cost_basis, str) or not (
            self.total_cost_basis.strip()
        ):
            raise ValueError("portfolio total_cost_basis is required")
        for value in (
            self.total_market_value,
            self.realized_pnl,
            self.unrealized_pnl,
            self.total_pnl,
            self.gross_exposure,
            self.long_exposure,
            self.short_exposure,
        ):
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(
                    "portfolio values must be None or non-empty text"
                )
        for value in (
            self.open_positions,
            self.working_orders,
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(
                    "portfolio counts must be nonnegative integers"
                )
        for value in (self.winning_positions, self.losing_positions):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(
                    "portfolio win/loss counts must be nonnegative or None"
                )
        for value in (
            self.largest_position,
            self.largest_unrealized_gain,
            self.largest_unrealized_loss,
        ):
            if value is not None and not isinstance(
                value,
                OperationsPortfolioHighlight,
            ):
                raise TypeError(
                    "portfolio highlights must be immutable highlights"
                )


@dataclass(frozen=True, slots=True)
class OperationsHealthState:
    runtime_status: str | None = None
    broker_status: str | None = None
    market_data_status: str | None = None
    trading_environment: str | None = None
    trading_rest_status: str | None = None
    account_status: str | None = None
    buying_power_status: str | None = None
    positions_status: str | None = None
    orders_status: str | None = None
    balances_status: str | None = None
    market_data_environment: str | None = None
    market_data_rest_status: str | None = None
    streaming_status: str | None = None
    subscription_status: str | None = None
    heartbeat_status: str | None = None
    reconnect_status: str | None = None
    entitlement_status: str | None = None
    market_session_status: str | None = None
    scanner_retry_status: str | None = None
    probe_aapl_status: str | None = None
    probe_spy_status: str | None = None
    probe_tsla_status: str | None = None
    probe_msft_status: str | None = None
    probe_nvda_status: str | None = None
    scanner_status: str | None = None
    universe_status: str | None = None
    symbols_status: str | None = None
    reference_cache_status: str | None = None
    ranking_status: str | None = None
    supported_symbols: int | None = None
    ai_status: str | None = None
    risk_status: str | None = None
    persistence_status: str | None = None
    last_error: str | None = None
    last_warning: str | None = None
    last_heartbeat: datetime | None = None
    connection_latency: str | None = None
    reconnect_attempts: int = 0
    degraded: bool = False
    healthy: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "runtime_status",
            "broker_status",
            "market_data_status",
            "trading_environment",
            "trading_rest_status",
            "account_status",
            "buying_power_status",
            "positions_status",
            "orders_status",
            "balances_status",
            "market_data_environment",
            "market_data_rest_status",
            "streaming_status",
            "subscription_status",
            "heartbeat_status",
            "reconnect_status",
            "entitlement_status",
            "market_session_status",
            "scanner_retry_status",
            "probe_aapl_status",
            "probe_spy_status",
            "probe_tsla_status",
            "probe_msft_status",
            "probe_nvda_status",
            "scanner_status",
            "universe_status",
            "symbols_status",
            "reference_cache_status",
            "ranking_status",
            "ai_status",
            "risk_status",
            "persistence_status",
            "last_error",
            "last_warning",
            "connection_latency",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
            ):
                raise ValueError(
                    f"health {field_name} must be None or stripped text"
                )
        if self.supported_symbols is not None and (
            isinstance(self.supported_symbols, bool)
            or not isinstance(self.supported_symbols, int)
            or self.supported_symbols < 0
        ):
            raise ValueError("health supported symbols must be nonnegative")
        if (
            self.last_heartbeat is not None
            and self.last_heartbeat.tzinfo is None
        ):
            raise ValueError("health heartbeat must be timezone-aware")
        if (
            isinstance(self.reconnect_attempts, bool)
            or not isinstance(self.reconnect_attempts, int)
            or self.reconnect_attempts < 0
        ):
            raise ValueError("health reconnect attempts must be nonnegative")
        if not isinstance(self.degraded, bool) or not isinstance(
            self.healthy,
            bool,
        ):
            raise TypeError("health flags must be bools")
        if self.degraded and self.healthy:
            raise ValueError("health cannot be healthy and degraded")


@dataclass(frozen=True, slots=True)
class OperationsWatchlistEntry:
    symbol: str
    latest_price: str | None = None
    change: str | None = None
    change_percent: str | None = None
    bid: str | None = None
    ask: str | None = None
    volume: int | None = None
    market_status: str | None = None
    last_update: datetime | None = None
    stale: bool | None = None
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.symbol, str)
            or not self.symbol.strip()
            or self.symbol != self.symbol.strip().upper()
        ):
            raise ValueError("watchlist symbol must be normalized")
        for field_name in (
            "latest_price",
            "change",
            "change_percent",
            "bid",
            "ask",
            "market_status",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
            ):
                raise ValueError(
                    f"watchlist {field_name} must be None or stripped text"
                )
        if self.volume is not None and (
            isinstance(self.volume, bool)
            or not isinstance(self.volume, int)
            or self.volume < 0
        ):
            raise ValueError("watchlist volume must be nonnegative")
        if self.last_update is not None and self.last_update.tzinfo is None:
            raise ValueError("watchlist last update must be timezone-aware")
        if self.stale is not None and not isinstance(self.stale, bool):
            raise TypeError("watchlist stale must be a bool or None")
        if not isinstance(self.metadata, tuple):
            raise TypeError("watchlist metadata must be an immutable tuple")


@dataclass(frozen=True, slots=True)
class OperationsWatchlistState:
    ordered_symbols: tuple[str, ...] = ()
    entries: tuple[OperationsWatchlistEntry, ...] = ()
    selected_symbol: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.ordered_symbols, tuple):
            raise TypeError("watchlist symbols must be an immutable tuple")
        if not isinstance(self.entries, tuple):
            raise TypeError("watchlist entries must be an immutable tuple")
        if any(
            not isinstance(entry, OperationsWatchlistEntry)
            for entry in self.entries
        ):
            raise TypeError(
                "watchlist entries must contain immutable entries"
            )
        if tuple(entry.symbol for entry in self.entries) != (
            self.ordered_symbols
        ):
            raise ValueError("watchlist entries must follow symbol order")
        if (
            self.selected_symbol is not None
            and self.selected_symbol not in self.ordered_symbols
        ):
            raise ValueError(
                "watchlist selected symbol must belong to the watchlist"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class WatchlistUpdated(OperationsEvent):
    state: OperationsWatchlistState

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)
        if not isinstance(self.state, OperationsWatchlistState):
            raise TypeError("state must be an OperationsWatchlistState")


@dataclass(frozen=True, slots=True, kw_only=True)
class HealthUpdated(OperationsEvent):
    state: OperationsHealthState

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)
        if not isinstance(self.state, OperationsHealthState):
            raise TypeError("state must be an OperationsHealthState")


@dataclass(frozen=True, slots=True, kw_only=True)
class PortfolioUpdated(OperationsEvent):
    summary: OperationsPortfolioSummary

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)
        if not isinstance(self.summary, OperationsPortfolioSummary):
            raise TypeError(
                "summary must be an OperationsPortfolioSummary"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionsUpdated(OperationsEvent):
    """Replace the projected decision slice with an immutable snapshot."""

    decisions: tuple[OperationsDecisionRecord, ...] = ()

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)
        if not isinstance(self.decisions, tuple):
            raise TypeError("decisions must be an immutable tuple")
        if any(
            not isinstance(item, OperationsDecisionRecord)
            for item in self.decisions
        ):
            raise TypeError(
                "decisions must contain OperationsDecisionRecord instances"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class TimelineUpdated(OperationsEvent):
    """Replace the projected runtime timeline with a bounded snapshot."""

    entries: tuple[OperationsTimelineEntry, ...] = ()

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)
        if not isinstance(self.entries, tuple):
            raise TypeError("timeline entries must be an immutable tuple")
        if any(
            not isinstance(entry, OperationsTimelineEntry)
            for entry in self.entries
        ):
            raise TypeError(
                "timeline entries must contain only "
                "OperationsTimelineEntry instances"
            )
        if any(
            first.timestamp < second.timestamp
            for first, second in zip(
                self.entries,
                self.entries[1:],
            )
        ):
            raise ValueError("timeline entries must be newest-first")


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
