from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from threading import RLock
from uuid import uuid4

from app.operations_core import (
    DecisionsUpdated,
    OperationsBus,
    OperationsEvent,
    RuntimeFailed,
    RuntimeStarting,
    RuntimeStopped,
    Subscription,
)

from .models import (
    RecordedEvent,
    RecordedSession,
    RecordingSnapshot,
    RecordingState,
    RecordingStatus,
)
from .serializer import RecordingSerializer


CompletedListener = Callable[[RecordedSession], None]


class SessionRecorder:
    """Record broker-neutral events once in bus publication order."""

    def __init__(
        self,
        bus: OperationsBus,
        serializer: RecordingSerializer,
        *,
        application_version: str,
        broker: str,
        runtime_mode: str,
        session_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(bus, OperationsBus):
            raise TypeError("bus must be an OperationsBus")
        if not isinstance(serializer, RecordingSerializer):
            raise TypeError(
                "serializer must be a RecordingSerializer"
            )
        for value, field_name in (
            (application_version, "application_version"),
            (broker, "broker"),
            (runtime_mode, "runtime_mode"),
        ):
            if (
                not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
            ):
                raise ValueError(
                    f"{field_name} must be stripped non-empty text"
                )
        if (
            session_id_factory is not None
            and not callable(session_id_factory)
        ):
            raise TypeError(
                "session_id_factory must be callable or None"
            )
        self._bus = bus
        self._serializer = serializer
        self._application_version = application_version
        self._broker = broker
        self._default_runtime_mode = runtime_mode
        self._session_id_factory = (
            session_id_factory or (lambda: str(uuid4()))
        )
        self._lock = RLock()
        self._events: tuple[RecordedEvent, ...] = ()
        self._session_id: str | None = None
        self._started_at: datetime | None = None
        self._last_event_at: datetime | None = None
        self._runtime_mode = runtime_mode
        self._strategy_version = "unknown"
        self._completed: RecordedSession | None = None
        self._listeners: dict[int, CompletedListener] = {}
        self._next_listener_id = 1
        self._closed = False
        self._subscription: Subscription | None = bus.subscribe(
            OperationsEvent,
            self._record,
        )

    def snapshot(self) -> RecordingSnapshot:
        with self._lock:
            if self._session_id is None:
                if self._completed is None:
                    return RecordingSnapshot.initial()
                session = self._completed
                return RecordingSnapshot(
                    state=RecordingState.STOPPED,
                    status=RecordingStatus.COMPLETED,
                    session_id=session.session_id,
                    started_at=session.started_at,
                    ended_at=session.ended_at,
                    duration_seconds=_duration(
                        session.started_at,
                        session.ended_at,
                    ),
                    event_count=len(session.events),
                    size_bytes=0,
                    file_path=None,
                    error=None,
                )
            return RecordingSnapshot(
                state=RecordingState.RECORDING,
                status=RecordingStatus.ACTIVE,
                session_id=self._session_id,
                started_at=self._started_at,
                ended_at=None,
                duration_seconds=_duration(
                    self._started_at,
                    self._last_event_at,
                ),
                event_count=len(self._events),
                size_bytes=0,
                file_path=None,
                error=None,
            )

    def completed_session(self) -> RecordedSession | None:
        with self._lock:
            return self._completed

    def subscribe_completed(
        self,
        listener: CompletedListener,
    ) -> int:
        if not callable(listener):
            raise TypeError("listener must be callable")
        with self._lock:
            listener_id = self._next_listener_id
            self._next_listener_id += 1
            self._listeners[listener_id] = listener
            return listener_id

    def unsubscribe_completed(self, listener_id: int) -> bool:
        with self._lock:
            return self._listeners.pop(listener_id, None) is not None

    def close(self) -> None:
        completed = None
        listeners: tuple[CompletedListener, ...] = ()
        with self._lock:
            if self._closed:
                return
            self._closed = True
        subscription = self._subscription
        if subscription is not None:
            self._bus.unsubscribe(subscription)
            self._subscription = None
        with self._lock:
            if (
                self._session_id is not None
                and self._last_event_at is not None
            ):
                completed = self._complete(self._last_event_at)
                listeners = tuple(self._listeners.values())
        if completed is not None:
            for listener in listeners:
                listener(completed)
        with self._lock:
            self._listeners.clear()

    def _record(self, event: OperationsEvent) -> None:
        completed = None
        listeners: tuple[CompletedListener, ...] = ()
        with self._lock:
            if self._session_id is None:
                self._start(event)
            if isinstance(event, RuntimeStarting):
                self._runtime_mode = event.environment
            if (
                isinstance(event, DecisionsUpdated)
                and event.decisions
            ):
                versions = {
                    decision.strategy_version
                    for decision in event.decisions
                }
                if len(versions) == 1:
                    self._strategy_version = next(iter(versions))
            recorded = self._serializer.record_event(
                event,
                len(self._events) + 1,
            )
            self._events = self._events + (recorded,)
            self._last_event_at = event.occurred_at
            if isinstance(event, (RuntimeStopped, RuntimeFailed)):
                completed = self._complete(event.occurred_at)
                listeners = tuple(self._listeners.values())
        if completed is not None:
            for listener in listeners:
                listener(completed)

    def _start(self, event: OperationsEvent) -> None:
        session_id = self._session_id_factory()
        if (
            not isinstance(session_id, str)
            or not session_id.strip()
            or session_id != session_id.strip()
        ):
            raise ValueError(
                "session_id_factory must return stripped non-empty text"
            )
        self._session_id = session_id
        self._started_at = event.occurred_at
        self._last_event_at = event.occurred_at
        self._events = ()
        self._runtime_mode = self._default_runtime_mode
        self._strategy_version = "unknown"

    def _complete(self, ended_at: datetime) -> RecordedSession:
        assert self._session_id is not None
        assert self._started_at is not None
        session = RecordedSession(
            session_id=self._session_id,
            started_at=min(
                self._started_at,
                *(event.timestamp for event in self._events),
            ),
            ended_at=max(
                ended_at,
                *(event.timestamp for event in self._events),
            ),
            strategy_version=self._strategy_version,
            application_version=self._application_version,
            broker=self._broker,
            runtime_mode=self._runtime_mode,
            events=self._events,
        )
        self._completed = session
        self._session_id = None
        self._started_at = None
        self._last_event_at = None
        self._events = ()
        return session


def _duration(
    started_at: datetime | None,
    ended_at: datetime | None,
) -> Decimal:
    if started_at is None or ended_at is None:
        return Decimal("0")
    return Decimal(str(max(0.0, (ended_at - started_at).total_seconds())))
