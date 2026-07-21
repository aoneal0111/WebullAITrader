from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from threading import Event
from typing import Protocol

from app.execution_coordinator import CoordinationRequest, ExecutionCoordinator
from app.paper_session import (
    PaperTradingSession,
    close_paper_session,
    create_paper_session,
    process_decision,
)
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
class PaperRuntimeEvent:
    sequence: int
    timestamp: datetime
    event_type: str
    message: str
    cycle: int
    symbol: str | None = None

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("runtime event sequence must be positive")
        _require_aware(self.timestamp)
        if not self.event_type.strip():
            raise ValueError("runtime event type is required")
        if not self.message.strip():
            raise ValueError("runtime event message is required")
        if self.cycle < 0:
            raise ValueError("runtime event cycle cannot be negative")
        if self.symbol is not None:
            normalized = self.symbol.strip().upper()
            if not normalized:
                raise ValueError("runtime event symbol cannot be blank")
            object.__setattr__(self, "symbol", normalized)


@dataclass(frozen=True, slots=True)
class PaperRuntimeCycleResult:
    cycle: int
    timestamp: datetime
    symbols: tuple[str, ...]
    decisions: tuple[StrategyDecision, ...]
    session: PaperTradingSession

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
        self._session: PaperTradingSession | None = None
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
        )
        engine._state = state
        engine._session = session
        return engine

    @property
    def state(self) -> PaperRuntimeState:
        return self._state

    @property
    def session(self) -> PaperTradingSession:
        if self._session is None:
            raise RuntimeError("paper runtime has not started")
        return self._session

    def start(self) -> PaperRuntimeState:
        if self._state.status is not PaperRuntimeStatus.CREATED:
            raise RuntimeError("paper runtime can only be started once")
        now = self._now()
        self._session = create_paper_session(
            session_id=self._session_id,
            initial_cash=self._initial_cash,
            started_at=now,
        )
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
            for index, snapshot in enumerate(snapshots, start=1):
                symbol = _symbol(snapshot)
                position = _strategy_position(session, symbol)
                decision = self._strategy_engine.evaluate(
                    snapshot,
                    position,
                    timestamp=timestamp,
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
                decisions.append(decision)
                self._append_event(
                    timestamp,
                    "DECISION_PROCESSED",
                    f"Processed {decision.action.value} for {symbol}.",
                    cycle,
                    symbol,
                )
            result = PaperRuntimeCycleResult(
                cycle=cycle,
                timestamp=timestamp,
                symbols=tuple(_symbol(snapshot) for snapshot in snapshots),
                decisions=tuple(decisions),
                session=session,
            )
            if self._cycle_sink is not None:
                self._cycle_sink(result)
            self._session = session
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
        now = self._now()
        self._session = close_paper_session(self.session, ended_at=now)
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
    ) -> None:
        event = PaperRuntimeEvent(
            sequence=len(self._state.events) + 1,
            timestamp=timestamp,
            event_type=event_type,
            message=message,
            cycle=cycle,
            symbol=symbol,
        )
        self._state = replace(
            self._state,
            events=(*self._state.events, event),
        )
        if self._event_sink is not None:
            self._event_sink(event)

    def _checkpoint(self) -> None:
        if self._checkpoint_sink is not None and self._session is not None:
            self._checkpoint_sink(self._state, self._session)


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
