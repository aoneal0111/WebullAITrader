from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from threading import RLock

from app.operations_core.bus import OperationsBus, Subscription
from app.operations_core.events import (
    OperationsEvent,
    PaperRuntimeSnapshot,
    PaperRuntimeUpdated,
    RuntimeCycleCompleted,
    RuntimeFailed,
    RuntimeStarted,
    RuntimeStarting,
    RuntimeStopped,
    RuntimeStopping,
)


class RuntimePhase(StrEnum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class RuntimeState:
    phase: RuntimePhase = RuntimePhase.STOPPED
    environment: str = "PAPER"
    broker_status: str = "Disconnected"
    market_feed_status: str = "Idle"
    inference_status: str = "Ready"
    active_model: str = "Not loaded"
    cycles_completed: int = 0
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    event_id: str
    occurred_at: datetime
    event_type: str
    source: str
    message: str


@dataclass(frozen=True, slots=True)
class ApplicationState:
    runtime: RuntimeState = field(default_factory=RuntimeState)
    paper_runtime: PaperRuntimeSnapshot | None = None
    timeline: tuple[TimelineEntry, ...] = ()
    revision: int = 0


StateListener = Callable[[ApplicationState], None]


class ApplicationStateStore:
    """
    Thread-safe single source of truth for Operations Center presentation state.

    It consumes immutable business events and publishes immutable state snapshots.
    """

    def __init__(
        self,
        bus: OperationsBus,
        *,
        timeline_limit: int = 500,
    ) -> None:
        if timeline_limit <= 0:
            raise ValueError("timeline_limit must be positive")

        self._bus = bus
        self._timeline_limit = timeline_limit
        self._lock = RLock()
        self._state = ApplicationState()
        self._listeners: dict[int, StateListener] = {}
        self._next_listener_id = 1
        self._subscription: Subscription = bus.subscribe(
            OperationsEvent,
            self._handle_event,
        )

    def snapshot(self) -> ApplicationState:
        with self._lock:
            return self._state

    def subscribe(self, listener: StateListener) -> int:
        if not callable(listener):
            raise TypeError("listener must be callable")

        with self._lock:
            listener_id = self._next_listener_id
            self._next_listener_id += 1
            self._listeners[listener_id] = listener
            state = self._state

        listener(state)
        return listener_id

    def unsubscribe(self, listener_id: int) -> bool:
        with self._lock:
            return self._listeners.pop(listener_id, None) is not None

    def close(self) -> None:
        self._bus.unsubscribe(self._subscription)

        with self._lock:
            self._listeners.clear()

    def _handle_event(self, event: OperationsEvent) -> None:
        with self._lock:
            runtime = self._reduce_runtime(self._state.runtime, event)
            paper_runtime = self._reduce_paper_runtime(
                self._state.paper_runtime,
                event,
            )

            timeline = self._state.timeline

            if not isinstance(
                event,
                (RuntimeCycleCompleted, PaperRuntimeUpdated),
            ):
                timeline = timeline + (self._timeline_entry(event),)
                timeline = timeline[-self._timeline_limit :]

            self._state = ApplicationState(
                runtime=runtime,
                paper_runtime=paper_runtime,
                timeline=timeline,
                revision=self._state.revision + 1,
            )

            state = self._state
            listeners = tuple(self._listeners.values())

        for listener in listeners:
            listener(state)

    @staticmethod
    def _reduce_paper_runtime(
        current: PaperRuntimeSnapshot | None,
        event: OperationsEvent,
    ) -> PaperRuntimeSnapshot | None:
        if isinstance(event, PaperRuntimeUpdated):
            return event.snapshot

        return current

    @staticmethod
    def _reduce_runtime(
        current: RuntimeState,
        event: OperationsEvent,
    ) -> RuntimeState:
        if isinstance(event, RuntimeStarting):
            return replace(
                current,
                phase=RuntimePhase.STARTING,
                environment=event.environment,
                broker_status="Connecting",
                market_feed_status="Starting",
                inference_status="Loading",
                cycles_completed=0,
                last_error=None,
            )

        if isinstance(event, RuntimeStarted):
            return replace(
                current,
                phase=RuntimePhase.RUNNING,
                environment=event.environment,
                broker_status="Connected",
                market_feed_status="Healthy",
                inference_status="Healthy",
                active_model=event.active_model,
                last_error=None,
            )

        if isinstance(event, RuntimeCycleCompleted):
            return replace(
                current,
                cycles_completed=event.cycle_count,
            )

        if isinstance(event, RuntimeStopping):
            return replace(
                current,
                phase=RuntimePhase.STOPPING,
            )

        if isinstance(event, RuntimeStopped):
            return replace(
                current,
                phase=RuntimePhase.STOPPED,
                broker_status="Disconnected",
                market_feed_status="Idle",
                inference_status="Ready",
                cycles_completed=event.cycles_completed,
                last_error=None,
            )

        if isinstance(event, RuntimeFailed):
            return replace(
                current,
                phase=RuntimePhase.FAILED,
                broker_status="Disconnected",
                market_feed_status="Error",
                inference_status="Error",
                last_error=event.error_message,
            )

        return current

    @staticmethod
    def _timeline_entry(event: OperationsEvent) -> TimelineEntry:
        if isinstance(event, RuntimeStarting):
            message = f"Starting {event.environment} runtime."
        elif isinstance(event, RuntimeStarted):
            message = (
                f"{event.environment} runtime started using "
                f"{event.active_model}."
            )
        elif isinstance(event, RuntimeStopping):
            message = event.reason
        elif isinstance(event, RuntimeStopped):
            message = (
                f"{event.reason} Cycles completed: "
                f"{event.cycles_completed}."
            )
        elif isinstance(event, RuntimeFailed):
            message = f"Runtime failed: {event.error_message}"
        else:
            message = type(event).__name__

        return TimelineEntry(
            event_id=str(event.event_id),
            occurred_at=event.occurred_at,
            event_type=type(event).__name__,
            source=event.source,
            message=message,
        )
