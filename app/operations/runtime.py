from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from threading import Event
from typing import Protocol

from app.capabilities import CapabilitySnapshot
from app.execution_coordinator import CoordinationRequest, ExecutionCoordinator
from app.operations_core import OperationsOrder
from app.operations.learning_runtime import (
    RuntimeInferenceAdapter,
    RuntimeInferenceAudit,
)
from app.operations.paper_lifecycle import PaperRuntimeSession
from app.paper_session import (
    PaperTradingSession,
    process_decision,
)
from app.paper_trading.models import PaperFill
from app.strategy_engine import StrategyDecision, StrategyEngine, StrategyPosition


class PaperRuntimeStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class SnapshotLike(Protocol):
    symbol: str


SnapshotSource = Callable[[datetime], Iterable[SnapshotLike]]
RequestBuilder = Callable[
    [StrategyDecision, SnapshotLike, PaperTradingSession, int, int],
    CoordinationRequest,
]
RuntimeEventSink = Callable[["PaperRuntimeEvent"], None]
CheckpointSink = Callable[["PaperRuntimeState", PaperTradingSession], None]
CycleSink = Callable[["PaperRuntimeCycleResult"], None]
Clock = Callable[[], datetime]
WaitFunction = Callable[[float], bool]


@dataclass(frozen=True, slots=True)
class RuntimeDecision:
    """Structured decision facts exposed at the runtime event boundary."""

    decision_id: str
    timestamp: datetime
    strategy_id: str
    symbol: str
    action: str
    confidence: int
    reasoning_summary: str
    risk_assessment: str | None = None
    requested_quantity: Decimal | None = None
    resulting_order_id: str | None = None

    def __post_init__(self) -> None:
        _require_aware(self.timestamp)
        for field_name in (
            "decision_id",
            "strategy_id",
            "symbol",
            "action",
            "reasoning_summary",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"runtime decision {field_name} is required")
            object.__setattr__(self, field_name, value.strip())
        object.__setattr__(self, "symbol", self.symbol.upper())
        object.__setattr__(self, "action", self.action.upper())
        if isinstance(self.confidence, bool) or not isinstance(
            self.confidence,
            int,
        ):
            raise TypeError("runtime decision confidence must be an integer")
        if not 0 <= self.confidence <= 100:
            raise ValueError(
                "runtime decision confidence must be between 0 and 100"
            )
        for field_name in ("risk_assessment", "resulting_order_id"):
            value = getattr(self, field_name)
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"runtime decision {field_name} must be non-empty text"
                    )
                object.__setattr__(self, field_name, value.strip())
        if self.requested_quantity is not None:
            if (
                not isinstance(self.requested_quantity, Decimal)
                or not self.requested_quantity.is_finite()
                or self.requested_quantity <= 0
            ):
                raise ValueError(
                    "runtime decision requested quantity must be positive"
                )


@dataclass(frozen=True, slots=True)
class RuntimeHealthUpdate:
    """Optional structured infrastructure facts carried by runtime events."""

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
    historical_bars_status: str | None = None
    quotes_status: str | None = None
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
    subscription_symbols: tuple[str, ...] | None = None
    ai_status: str | None = None
    risk_status: str | None = None
    persistence_status: str | None = None
    last_error: str | None = None
    last_warning: str | None = None
    heartbeat_at: datetime | None = None
    connection_latency: Decimal | None = None
    reconnect_attempts: int | None = None
    capabilities: CapabilitySnapshot | None = None

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
            "historical_bars_status",
            "quotes_status",
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
        ):
            value = getattr(self, field_name)
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"runtime health {field_name} must be non-empty text"
                    )
                object.__setattr__(self, field_name, value.strip())
        if self.supported_symbols is not None and (
            isinstance(self.supported_symbols, bool)
            or not isinstance(self.supported_symbols, int)
            or self.supported_symbols < 0
        ):
            raise ValueError("runtime health supported symbols must be nonnegative")
        if self.subscription_symbols is not None:
            if not isinstance(self.subscription_symbols, tuple):
                raise TypeError("runtime health subscription symbols must be a tuple")
            normalized = tuple(
                symbol.strip().upper() for symbol in self.subscription_symbols
            )
            if any(not symbol for symbol in normalized) or len(set(normalized)) != len(normalized):
                raise ValueError("runtime health subscription symbols must be unique symbols")
            object.__setattr__(self, "subscription_symbols", normalized)
        if self.capabilities is not None and not isinstance(
            self.capabilities,
            CapabilitySnapshot,
        ):
            raise TypeError("runtime health capabilities must be a snapshot")
        if self.heartbeat_at is not None:
            _require_aware(self.heartbeat_at)
        if self.connection_latency is not None and (
            not isinstance(self.connection_latency, Decimal)
            or not self.connection_latency.is_finite()
            or self.connection_latency < 0
        ):
            raise ValueError(
                "runtime health connection latency must be nonnegative"
            )
        if self.reconnect_attempts is not None and (
            isinstance(self.reconnect_attempts, bool)
            or not isinstance(self.reconnect_attempts, int)
            or self.reconnect_attempts < 0
        ):
            raise ValueError(
                "runtime health reconnect attempts must be nonnegative"
            )


@dataclass(frozen=True, slots=True)
class RuntimeWatchlistQuote:
    timestamp: datetime
    latest_price: Decimal | None = None
    change: Decimal | None = None
    change_percent: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    volume: int | None = None
    stale: bool | None = None

    def __post_init__(self) -> None:
        _require_aware(self.timestamp)
        for field_name in (
            "latest_price",
            "change",
            "change_percent",
            "bid",
            "ask",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, Decimal)
                or not value.is_finite()
            ):
                raise ValueError(
                    f"watchlist quote {field_name} must be a finite Decimal"
                )
        for field_name in ("latest_price", "bid", "ask"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(
                    f"watchlist quote {field_name} must be nonnegative"
                )
        if self.volume is not None and (
            isinstance(self.volume, bool)
            or not isinstance(self.volume, int)
            or self.volume < 0
        ):
            raise ValueError("watchlist quote volume must be nonnegative")
        if self.stale is not None and not isinstance(self.stale, bool):
            raise TypeError("watchlist quote stale must be a bool or None")


@dataclass(frozen=True, slots=True)
class RuntimeWatchlistUpdate:
    symbol: str | None = None
    subscribed: bool | None = None
    quote: RuntimeWatchlistQuote | None = None
    market_status: str | None = None
    selection_changed: bool = False
    selected_symbol: str | None = None
    metadata: tuple[tuple[str, str], ...] | None = None

    def __post_init__(self) -> None:
        if self.symbol is not None:
            symbol = self.symbol.strip().upper()
            if not symbol:
                raise ValueError("watchlist update symbol cannot be blank")
            object.__setattr__(self, "symbol", symbol)
        if self.subscribed is not None and not isinstance(
            self.subscribed,
            bool,
        ):
            raise TypeError("watchlist subscribed must be a bool or None")
        if self.quote is not None and not isinstance(
            self.quote,
            RuntimeWatchlistQuote,
        ):
            raise TypeError("watchlist quote must be a RuntimeWatchlistQuote")
        if self.market_status is not None:
            status = self.market_status.strip().upper()
            if not status:
                raise ValueError("watchlist market status cannot be blank")
            object.__setattr__(self, "market_status", status)
        if not isinstance(self.selection_changed, bool):
            raise TypeError("selection_changed must be a bool")
        if self.selected_symbol is not None:
            selected = self.selected_symbol.strip().upper()
            if not selected:
                raise ValueError("selected symbol cannot be blank")
            object.__setattr__(self, "selected_symbol", selected)
        if self.metadata is not None:
            if not isinstance(self.metadata, tuple):
                raise TypeError("watchlist metadata must be an immutable tuple")
            normalized = tuple(
                (_required_metadata(key), _required_metadata(value))
                for key, value in self.metadata
            )
            if len({key for key, _ in normalized}) != len(normalized):
                raise ValueError("watchlist metadata keys must be unique")
            object.__setattr__(self, "metadata", normalized)
        has_symbol_fact = any(
            value is not None
            for value in (
                self.subscribed,
                self.quote,
                self.metadata,
            )
        )
        if has_symbol_fact and self.symbol is None:
            raise ValueError(
                "watchlist membership, quote, and metadata require a symbol"
            )


@dataclass(frozen=True, slots=True)
class PaperRuntimeEvent:
    sequence: int
    timestamp: datetime
    event_type: str
    message: str
    cycle: int
    symbol: str | None = None
    order: OperationsOrder | None = None
    fill: PaperFill | None = None
    mark_price: Decimal | None = None
    source: str = "paper-runtime"
    decision: RuntimeDecision | None = None
    health: RuntimeHealthUpdate | None = None
    watchlist: RuntimeWatchlistUpdate | None = None

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("runtime event sequence must be positive")
        _require_aware(self.timestamp)
        if not self.event_type.strip():
            raise ValueError("runtime event type is required")
        if not self.message.strip():
            raise ValueError("runtime event message is required")
        if not self.source.strip() or self.source != self.source.strip():
            raise ValueError(
                "runtime event source must be non-empty stripped text"
            )
        if self.cycle < 0:
            raise ValueError("runtime event cycle cannot be negative")
        if self.symbol is not None:
            normalized = self.symbol.strip().upper()
            if not normalized:
                raise ValueError("runtime event symbol cannot be blank")
            object.__setattr__(self, "symbol", normalized)
        if self.order is not None:
            if not isinstance(self.order, OperationsOrder):
                raise TypeError("runtime event order must be an OperationsOrder")
            if self.symbol is not None and self.order.symbol != self.symbol:
                raise ValueError(
                    "runtime event order symbol must match event symbol"
                )
        if self.fill is not None:
            if not isinstance(self.fill, PaperFill):
                raise TypeError("runtime event fill must be a PaperFill")
            if self.symbol is not None and self.fill.symbol != self.symbol:
                raise ValueError(
                    "runtime event fill symbol must match event symbol"
                )
        if self.mark_price is not None:
            if (
                not isinstance(self.mark_price, Decimal)
                or not self.mark_price.is_finite()
                or self.mark_price <= 0
            ):
                raise ValueError(
                    "runtime event mark price must be a positive finite Decimal"
                )
            if self.fill is None and self.symbol is None:
                raise ValueError(
                    "runtime event mark price requires a fill or symbol"
                )
        if self.decision is not None:
            if not isinstance(self.decision, RuntimeDecision):
                raise TypeError(
                    "runtime event decision must be a RuntimeDecision"
                )
            if self.symbol is not None and self.decision.symbol != self.symbol:
                raise ValueError(
                    "runtime event decision symbol must match event symbol"
                )
        if self.health is not None and not isinstance(
            self.health,
            RuntimeHealthUpdate,
        ):
            raise TypeError(
                "runtime event health must be a RuntimeHealthUpdate"
            )
        if self.watchlist is not None and not isinstance(
            self.watchlist,
            RuntimeWatchlistUpdate,
        ):
            raise TypeError(
                "runtime event watchlist must be a RuntimeWatchlistUpdate"
            )


def _required_metadata(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("watchlist metadata must be non-empty text")
    return value.strip()


@dataclass(frozen=True, slots=True)
class PaperRuntimeCycleResult:
    cycle: int
    timestamp: datetime
    symbols: tuple[str, ...]
    decisions: tuple[StrategyDecision, ...]
    session: PaperTradingSession
    inference_audits: tuple[RuntimeInferenceAudit, ...] = ()

    def __post_init__(self) -> None:
        if self.cycle < 1:
            raise ValueError("runtime cycle must be positive")
        _require_aware(self.timestamp)
        if len(self.symbols) != len(self.decisions):
            raise ValueError("runtime cycle symbols and decisions must align")
        normalized = tuple(symbol.strip().upper() for symbol in self.symbols)
        if any(not symbol for symbol in normalized):
            raise ValueError("runtime cycle symbols cannot be blank")
        if len(set(normalized)) != len(normalized):
            raise ValueError("runtime cycle symbols must be unique")
        if tuple(decision.symbol for decision in self.decisions) != normalized:
            raise ValueError("runtime cycle decisions do not match symbols")
        if self.inference_audits and len(self.inference_audits) != len(normalized):
            raise ValueError(
                "runtime cycle inference audits must align with symbols"
            )
        if self.inference_audits and tuple(
            audit.symbol for audit in self.inference_audits
        ) != normalized:
            raise ValueError(
                "runtime cycle inference audits do not match symbols"
            )
        object.__setattr__(self, "symbols", normalized)


@dataclass(frozen=True, slots=True)
class PaperRuntimeState:
    status: PaperRuntimeStatus
    session_id: str
    started_at: datetime | None
    stopped_at: datetime | None
    cycles_completed: int
    snapshots_processed: int
    decisions_processed: int
    last_cycle_at: datetime | None
    failure: str | None
    events: tuple[PaperRuntimeEvent, ...] = ()

    def __post_init__(self) -> None:
        session_id = self.session_id.strip()
        if not session_id:
            raise ValueError("session ID is required")
        object.__setattr__(self, "session_id", session_id)
        for value in (self.started_at, self.stopped_at, self.last_cycle_at):
            if value is not None:
                _require_aware(value)
        if min(
            self.cycles_completed,
            self.snapshots_processed,
            self.decisions_processed,
        ) < 0:
            raise ValueError("runtime counters cannot be negative")
        if self.status is PaperRuntimeStatus.CREATED and self.started_at is not None:
            raise ValueError("created runtime cannot have a start time")
        if self.status is PaperRuntimeStatus.STOPPED and self.stopped_at is None:
            raise ValueError("stopped runtime requires a stop time")
        if self.status is not PaperRuntimeStatus.STOPPED and self.stopped_at is not None:
            raise ValueError("only a stopped runtime may have a stop time")


class PaperOperationsEngine:
    """Run the deterministic strategy/coordinator/paper-session pipeline.

    The engine has no broker mutation capability and does not manufacture order
    quantities, risk approvals, compliance approvals, or market state. Those
    values must be supplied by the injected request builder.
    """

    def __init__(
        self,
        *,
        session_id: str,
        initial_cash,
        snapshot_source: SnapshotSource,
        strategy_engine: StrategyEngine,
        coordinator: ExecutionCoordinator,
        request_builder: RequestBuilder,
        clock: Clock,
        event_sink: RuntimeEventSink | None = None,
        checkpoint_sink: CheckpointSink | None = None,
        cycle_sink: CycleSink | None = None,
        inference_adapter: RuntimeInferenceAdapter | None = None,
        session_lifecycle: PaperRuntimeSession | None = None,
        experiment_journal: object | None = None,
    ) -> None:
        if not session_id.strip():
            raise ValueError("session ID is required")
        self._session_id = session_id.strip()
        self._initial_cash = initial_cash
        self._snapshot_source = snapshot_source
        self._strategy_engine = strategy_engine
        self._coordinator = coordinator
        self._request_builder = request_builder
        self._clock = clock
        self._event_sink = event_sink
        self._checkpoint_sink = checkpoint_sink
        self._cycle_sink = cycle_sink
        self._inference_adapter = inference_adapter
        if experiment_journal is not None and not callable(
            getattr(experiment_journal, "record_coordination_result", None)
        ):
            raise TypeError("experiment_journal must record coordination results")
        self._experiment_journal = experiment_journal

        if session_lifecycle is not None:
            if not isinstance(session_lifecycle, PaperRuntimeSession):
                raise TypeError(
                    "session_lifecycle must be PaperRuntimeSession"
                )
            if session_lifecycle.session_id != self._session_id:
                raise ValueError(
                    "session lifecycle ID does not match runtime session ID"
                )
            if session_lifecycle.initial_cash != self._initial_cash:
                raise ValueError(
                    "session lifecycle initial cash does not match runtime"
                )

        self._session_lifecycle = (
            session_lifecycle
            if session_lifecycle is not None
            else PaperRuntimeSession(
                session_id=self._session_id,
                initial_cash=self._initial_cash,
                clock=self._clock,
            )
        )
        self._state = PaperRuntimeState(
            status=PaperRuntimeStatus.CREATED,
            session_id=self._session_id,
            started_at=None,
            stopped_at=None,
            cycles_completed=0,
            snapshots_processed=0,
            decisions_processed=0,
            last_cycle_at=None,
            failure=None,
        )

    @classmethod
    def recover(
        cls,
        *,
        state: PaperRuntimeState,
        session: PaperTradingSession,
        snapshot_source: SnapshotSource,
        strategy_engine: StrategyEngine,
        coordinator: ExecutionCoordinator,
        request_builder: RequestBuilder,
        clock: Clock,
        event_sink: RuntimeEventSink | None = None,
        checkpoint_sink: CheckpointSink | None = None,
        cycle_sink: CycleSink | None = None,
        inference_adapter: RuntimeInferenceAdapter | None = None,
        experiment_journal: object | None = None,
    ) -> "PaperOperationsEngine":
        _validate_recovery(state, session)
        engine = cls(
            session_id=state.session_id,
            initial_cash=session.portfolio.initial_cash,
            snapshot_source=snapshot_source,
            strategy_engine=strategy_engine,
            coordinator=coordinator,
            request_builder=request_builder,
            clock=clock,
            event_sink=event_sink,
            checkpoint_sink=checkpoint_sink,
            cycle_sink=cycle_sink,
            inference_adapter=inference_adapter,
            experiment_journal=experiment_journal,
            session_lifecycle=PaperRuntimeSession(
                session_id=state.session_id,
                initial_cash=session.portfolio.initial_cash,
                clock=clock,
                session=session,
            ),
        )
        engine._state = state
        return engine

    @property
    def state(self) -> PaperRuntimeState:
        return self._state

    @property
    def session(self) -> PaperTradingSession:
        if self._session_lifecycle.session is None:
            raise RuntimeError("paper runtime has not started")
        return self._session_lifecycle.session

    def start(self) -> PaperRuntimeState:
        if self._state.status is not PaperRuntimeStatus.CREATED:
            raise RuntimeError("paper runtime can only be started once")
        session = self._session_lifecycle.start()
        now = session.started_at
        self._state = replace(
            self._state,
            status=PaperRuntimeStatus.RUNNING,
            started_at=now,
        )
        self._append_event(now, "STARTED", "Paper runtime started.", 0)
        self._checkpoint()
        return self._state

    def pause(self) -> PaperRuntimeState:
        self._require_status(PaperRuntimeStatus.RUNNING)
        now = self._now()
        self._state = replace(self._state, status=PaperRuntimeStatus.PAUSED)
        self._append_event(
            now,
            "PAUSED",
            "Paper runtime paused.",
            self._state.cycles_completed,
        )
        self._checkpoint()
        return self._state

    def resume(self) -> PaperRuntimeState:
        self._require_status(PaperRuntimeStatus.PAUSED)
        now = self._now()
        self._state = replace(self._state, status=PaperRuntimeStatus.RUNNING)
        self._append_event(
            now,
            "RESUMED",
            "Paper runtime resumed.",
            self._state.cycles_completed,
        )
        self._checkpoint()
        return self._state

    def run_cycle(self) -> PaperTradingSession:
        self._require_status(PaperRuntimeStatus.RUNNING)
        timestamp = self._now()
        if self._state.last_cycle_at is not None and timestamp < self._state.last_cycle_at:
            raise ValueError("runtime clock moved backwards")

        cycle = self._state.cycles_completed + 1
        try:
            snapshots = tuple(sorted(
                self._snapshot_source(timestamp),
                key=lambda item: _symbol(item),
            ))
            session = self.session
            decisions: list[StrategyDecision] = []
            inference_audits: list[RuntimeInferenceAudit] = []

            if self._inference_adapter is not None:
                self._inference_adapter.begin_cycle(cycle)

            for index, snapshot in enumerate(snapshots, start=1):
                symbol = _symbol(snapshot)
                position = _strategy_position(session, symbol)
                decision = self._strategy_engine.evaluate(
                    snapshot,
                    position,
                    timestamp=timestamp,
                )

                inference_audit = None
                if self._inference_adapter is not None:
                    inference_audit = self._inference_adapter.evaluate(
                        snapshot=snapshot,
                        session=session,
                        cycle=cycle,
                        symbol_index=index,
                    )
                    inference_audits.append(inference_audit)

                    if (
                        decision.creates_order_intent
                        and not inference_audit.allows_order_intent
                    ):
                        decision = _veto_to_hold(decision)
                        self._append_event(
                            timestamp,
                            "INFERENCE_VETO",
                            (
                                "Model inference vetoed executable intent: "
                                f"{inference_audit.reason}."
                            ),
                            cycle,
                            symbol,
                        )

                request = None
                if decision.creates_order_intent:
                    request = self._request_builder(
                        decision,
                        snapshot,
                        session,
                        cycle,
                        index,
                    )
                    if not isinstance(request, CoordinationRequest):
                        raise TypeError(
                            "request builder must return CoordinationRequest "
                            "for executable decisions"
                        )
                session = process_decision(
                    session,
                    coordinator=self._coordinator,
                    strategy_decision=decision,
                    request=request,
                )
                if self._experiment_journal is not None:
                    self._experiment_journal.record_coordination_result(
                        session.last_coordination_result
                    )
                decisions.append(decision)
                order = _project_runtime_order(
                    session.last_coordination_result,
                    timestamp,
                )
                fill, mark_price = _project_runtime_fill(
                    session.last_coordination_result,
                )
                runtime_decision = _project_runtime_decision(
                    session.last_coordination_result,
                    decision,
                    decision_id=(
                        f"{self._state.session_id}:{cycle}:{index}:{symbol}"
                    ),
                )
                self._append_event(
                    timestamp,
                    "DECISION_PROCESSED",
                    f"Processed {decision.action.value} for {symbol}.",
                    cycle,
                    symbol,
                    order,
                    fill,
                    mark_price,
                    runtime_decision,
                )
            result = PaperRuntimeCycleResult(
                cycle=cycle,
                timestamp=timestamp,
                symbols=tuple(_symbol(snapshot) for snapshot in snapshots),
                decisions=tuple(decisions),
                session=session,
                inference_audits=tuple(inference_audits),
            )
            if self._cycle_sink is not None:
                self._cycle_sink(result)
            self._session_lifecycle.update(session)
            self._state = replace(
                self._state,
                cycles_completed=cycle,
                snapshots_processed=(
                    self._state.snapshots_processed + len(snapshots)
                ),
                decisions_processed=(
                    self._state.decisions_processed + len(snapshots)
                ),
                last_cycle_at=timestamp,
            )
            self._append_event(
                timestamp,
                "CYCLE_COMPLETED",
                f"Completed cycle {cycle} with {len(snapshots)} snapshots.",
                cycle,
            )
            self._checkpoint()
            return self.session
        except Exception as exc:
            self._state = replace(
                self._state,
                status=PaperRuntimeStatus.FAILED,
                failure=f"{type(exc).__name__}: {exc}",
            )
            self._append_event(
                timestamp,
                "FAILED",
                "Paper runtime failed closed.",
                cycle,
            )
            self._checkpoint()
            raise

    def run(
        self,
        *,
        interval_seconds: float,
        stop_event: Event | None = None,
        max_cycles: int | None = None,
        wait: WaitFunction | None = None,
    ) -> PaperRuntimeState:
        if interval_seconds < 0:
            raise ValueError("interval seconds cannot be negative")
        if max_cycles is not None and max_cycles < 1:
            raise ValueError("max cycles must be positive")
        self._require_status(PaperRuntimeStatus.RUNNING)
        external_stop = stop_event or Event()
        waiter = wait or external_stop.wait
        completed = 0
        while not external_stop.is_set():
            if self._state.status is PaperRuntimeStatus.PAUSED:
                if waiter(interval_seconds):
                    break
                continue
            if self._state.status is not PaperRuntimeStatus.RUNNING:
                break
            self.run_cycle()
            completed += 1
            if max_cycles is not None and completed >= max_cycles:
                break
            if waiter(interval_seconds):
                break
        return self._state

    def stop(self) -> PaperTradingSession:
        if self._state.status not in {
            PaperRuntimeStatus.RUNNING,
            PaperRuntimeStatus.PAUSED,
            PaperRuntimeStatus.FAILED,
        }:
            raise RuntimeError("paper runtime is not active")
        session = self._session_lifecycle.close()
        assert session is not None
        assert session.ended_at is not None
        now = session.ended_at
        self._state = replace(
            self._state,
            status=PaperRuntimeStatus.STOPPED,
            stopped_at=now,
        )
        self._append_event(
            now,
            "STOPPED",
            "Paper runtime stopped.",
            self._state.cycles_completed,
        )
        self._checkpoint()
        return self.session

    def _now(self) -> datetime:
        value = self._clock()
        _require_aware(value)
        return value

    def _require_status(self, expected: PaperRuntimeStatus) -> None:
        if self._state.status is not expected:
            raise RuntimeError(
                f"paper runtime must be {expected.value}; "
                f"current status is {self._state.status.value}"
            )

    def _append_event(
        self,
        timestamp: datetime,
        event_type: str,
        message: str,
        cycle: int,
        symbol: str | None = None,
        order: OperationsOrder | None = None,
        fill: PaperFill | None = None,
        mark_price: Decimal | None = None,
        decision: RuntimeDecision | None = None,
    ) -> None:
        event = PaperRuntimeEvent(
            sequence=len(self._state.events) + 1,
            timestamp=timestamp,
            event_type=event_type,
            message=message,
            cycle=cycle,
            symbol=symbol,
            order=order,
            fill=fill,
            mark_price=mark_price,
            decision=decision,
        )
        self._state = replace(
            self._state,
            events=(*self._state.events, event),
        )
        if self._event_sink is not None:
            self._event_sink(event)

    def _checkpoint(self) -> None:
        session = self._session_lifecycle.session
        if self._checkpoint_sink is not None and session is not None:
            self._checkpoint_sink(self._state, session)


def _veto_to_hold(decision: StrategyDecision) -> StrategyDecision:
    """Return an immutable non-executable form of a strategy decision."""

    action_type = type(decision.action)
    try:
        hold_action = action_type.HOLD
    except AttributeError as exc:
        raise TypeError(
            "strategy decision action type must define HOLD"
        ) from exc

    changes = {"action": hold_action}

    fields = getattr(decision, "__dataclass_fields__", {})
    if "order_intent" in fields:
        changes["order_intent"] = None
    elif "intent" in fields:
        changes["intent"] = None

    vetoed = replace(decision, **changes)
    if vetoed.creates_order_intent:
        raise ValueError(
            "inference-vetoed strategy decision remained executable"
        )
    return vetoed


def _project_runtime_order(
    coordination,
    timestamp: datetime,
) -> OperationsOrder | None:
    """Expose explicit immutable order facts on the runtime event boundary."""

    if coordination is None or coordination.order_intent is None:
        return None

    intent = coordination.order_intent
    status = coordination.status.value
    execution_result = coordination.execution_result
    execution = getattr(execution_result, "execution", None)
    execution_status = getattr(execution, "status", None)
    if execution_status is not None:
        status = execution_status.value

    return OperationsOrder(
        order_id=intent.request_id,
        symbol=intent.symbol,
        side=intent.side.value,
        quantity=format(intent.quantity, "f"),
        status=status,
        updated_at=timestamp,
        order_type=getattr(intent.order_type, "value", str(intent.order_type)),
        limit_price=(
            None
            if intent.limit_price is None
            else format(intent.limit_price, "f")
        ),
        stop_price=(
            None
            if intent.stop_price is None
            else format(intent.stop_price, "f")
        ),
        submitted_at=timestamp,
        execution_source="RUNTIME",
    )


def _project_runtime_decision(
    coordination,
    decision: StrategyDecision,
    *,
    decision_id: str,
) -> RuntimeDecision:
    """Expose explicit strategy and risk facts without parsing event text."""

    intent = (
        coordination.order_intent
        if coordination is not None
        else None
    )
    risk_decision = (
        coordination.risk_decision
        if coordination is not None
        else None
    )
    risk_assessment = None
    primary_reason = getattr(risk_decision, "primary_reason", None)
    approval_reason = getattr(risk_decision, "approval_reason", None)
    if primary_reason is not None:
        risk_assessment = getattr(primary_reason, "value", str(primary_reason))
    elif isinstance(approval_reason, str) and approval_reason.strip():
        risk_assessment = approval_reason.strip()

    action = (
        intent.side.value
        if intent is not None
        else decision.action.value
    )
    reasons = "; ".join(decision.reasons)
    return RuntimeDecision(
        decision_id=(
            intent.request_id
            if intent is not None
            else decision_id
        ),
        timestamp=decision.timestamp,
        strategy_id=decision.strategy_version,
        symbol=decision.symbol,
        action=action,
        confidence=decision.confidence,
        reasoning_summary=reasons,
        risk_assessment=risk_assessment,
        requested_quantity=(
            intent.quantity
            if intent is not None
            else None
        ),
        resulting_order_id=(
            intent.request_id
            if intent is not None
            else None
        ),
    )


def _project_runtime_fill(
    coordination,
) -> tuple[PaperFill | None, Decimal | None]:
    """Expose fill and available post-fill mark on the event boundary."""

    if coordination is None:
        return None, None

    execution_result = coordination.execution_result
    execution = getattr(execution_result, "execution", None)
    fill = getattr(execution, "fill", None)
    if fill is None:
        return None, None
    if not isinstance(fill, PaperFill):
        raise TypeError("paper execution fill must be a PaperFill")

    portfolio = getattr(execution, "portfolio_after", None)
    positions = getattr(portfolio, "positions", ())
    mark_price = next(
        (
            position.current_mark
            for position in positions
            if position.symbol == fill.symbol
        ),
        None,
    )
    return fill, mark_price


def _validate_recovery(
    state: PaperRuntimeState,
    session: PaperTradingSession,
) -> None:
    if state.status not in {
        PaperRuntimeStatus.RUNNING,
        PaperRuntimeStatus.PAUSED,
        PaperRuntimeStatus.FAILED,
    }:
        raise ValueError("only active or failed runtimes can be recovered")
    if session.status.value != "ACTIVE":
        raise ValueError("recovered paper session must be active")
    if state.session_id != session.session_id:
        raise ValueError("runtime and paper session IDs do not match")
    if state.started_at != session.started_at:
        raise ValueError("runtime and paper session start times do not match")
    if state.stopped_at is not None:
        raise ValueError("recovered runtime cannot have a stop time")
    if state.last_cycle_at is not None and state.last_cycle_at < session.started_at:
        raise ValueError("last cycle cannot precede session start")
    if state.decisions_processed != session.statistics.decisions_processed:
        raise ValueError("runtime and session decision counts do not match")


def _strategy_position(
    session: PaperTradingSession,
    symbol: str,
) -> StrategyPosition:
    for position in session.portfolio.positions:
        if position.symbol.strip().upper() == symbol:
            return StrategyPosition(symbol, position.quantity)
    return StrategyPosition(symbol)


def _symbol(snapshot: SnapshotLike) -> str:
    value = snapshot.symbol.strip().upper()
    if not value:
        raise ValueError("snapshot symbol is required")
    return value


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("runtime timestamps must be timezone-aware")
