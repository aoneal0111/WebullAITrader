from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from threading import RLock

from app.replay import ReplayController

from .models import (
    EventStoreSnapshot,
    EventStoreStatus,
    QueryResult,
)
from .query import EventStoreQueryEngine
from .repository import EventStoreRepository


EventStoreListener = Callable[[EventStoreSnapshot], None]


class EventStoreController:
    def __init__(
        self,
        repository: EventStoreRepository,
        query_engine: EventStoreQueryEngine,
        replay_controller: ReplayController,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(repository, EventStoreRepository):
            raise TypeError(
                "repository must be an EventStoreRepository"
            )
        if not isinstance(query_engine, EventStoreQueryEngine):
            raise TypeError(
                "query_engine must be EventStoreQueryEngine"
            )
        if not isinstance(replay_controller, ReplayController):
            raise TypeError(
                "replay_controller must be a ReplayController"
            )
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable or None")
        self._repository = repository
        self._query_engine = query_engine
        self._replay_controller = replay_controller
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._snapshot = EventStoreSnapshot.initial()
        self._listeners: dict[int, EventStoreListener] = {}
        self._next_listener_id = 1
        self._closed = False
        self.refresh()

    def snapshot(self) -> EventStoreSnapshot:
        with self._lock:
            return self._snapshot

    def refresh(self) -> EventStoreSnapshot:
        try:
            index = self._repository.refresh()
        except (OSError, ValueError) as exc:
            with self._lock:
                self._snapshot = EventStoreSnapshot(
                    status=EventStoreStatus.ERROR,
                    sessions=self._snapshot.sessions,
                    result=self._snapshot.result,
                    statistics=self._snapshot.statistics,
                    available_symbols=self._snapshot.available_symbols,
                    available_event_types=(
                        self._snapshot.available_event_types
                    ),
                    last_refresh=self._snapshot.last_refresh,
                    errors=(str(exc),),
                )
            self._notify()
            return self.snapshot()
        result = self._query_engine.query_all(index)
        status = (
            EventStoreStatus.READY
            if index.sessions
            else EventStoreStatus.EMPTY
        )
        with self._lock:
            self._snapshot = EventStoreSnapshot(
                status=status,
                sessions=index.sessions,
                result=result,
                statistics=result.statistics,
                available_symbols=tuple(
                    key for key, _ in index.symbol_index
                ),
                available_event_types=tuple(
                    key for key, _ in index.event_type_index
                ),
                last_refresh=self._clock(),
                errors=(),
            )
        self._notify()
        return self.snapshot()

    def query(self, method: str, *values) -> QueryResult:
        with self._lock:
            self._ensure_open()
        index = self._repository.index
        operations = {
            "all": self._query_engine.query_all,
            "search": self._query_engine.search,
            "symbol": self._query_engine.by_symbol,
            "event_type": self._query_engine.by_event_type,
            "session": self._query_engine.by_session,
            "time": self._query_engine.by_timestamp_range,
            "order": self._query_engine.by_order_id,
            "position": self._query_engine.by_position_id,
            "decision": self._query_engine.by_decision,
            "lifecycle": self._query_engine.by_lifecycle_phase,
        }
        operation = operations.get(method)
        if operation is None:
            raise ValueError(f"unsupported query method: {method}")
        result = operation(index, *values)
        with self._lock:
            self._snapshot = EventStoreSnapshot(
                status=self._snapshot.status,
                sessions=self._snapshot.sessions,
                result=result,
                statistics=self._snapshot.statistics,
                available_symbols=self._snapshot.available_symbols,
                available_event_types=self._snapshot.available_event_types,
                last_refresh=self._snapshot.last_refresh,
                errors=self._snapshot.errors,
            )
        self._notify()
        return result

    def statistics(self):
        return self._query_engine.statistics(self._repository.index)

    def open_replay(self, session_id: str) -> None:
        archive = self._repository.archive(session_id)
        self._replay_controller.load(
            archive,
            session_id=session_id,
        )

    def subscribe(self, listener: EventStoreListener) -> int:
        if not callable(listener):
            raise TypeError("listener must be callable")
        with self._lock:
            self._ensure_open()
            listener_id = self._next_listener_id
            self._next_listener_id += 1
            self._listeners[listener_id] = listener
            snapshot = self._snapshot
        listener(snapshot)
        return listener_id

    def unsubscribe(self, listener_id: int) -> bool:
        with self._lock:
            return self._listeners.pop(listener_id, None) is not None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._listeners.clear()
            self._closed = True
            self._snapshot = EventStoreSnapshot(
                status=EventStoreStatus.CLOSED,
                sessions=self._snapshot.sessions,
                result=self._snapshot.result,
                statistics=self._snapshot.statistics,
                available_symbols=self._snapshot.available_symbols,
                available_event_types=self._snapshot.available_event_types,
                last_refresh=self._snapshot.last_refresh,
                errors=self._snapshot.errors,
            )
        self._repository.close()

    def _notify(self) -> None:
        with self._lock:
            snapshot = self._snapshot
            listeners = tuple(self._listeners.values())
        for listener in listeners:
            listener(snapshot)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("event store controller is closed")
