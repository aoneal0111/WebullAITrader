from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from threading import RLock

from app.event_store import EventStoreSnapshot

from .domain_models import AnalyticsStatus
from .engine import AnalyticsEngine
from .models import AnalyticsSnapshot
from .repository import AnalyticsRepository


AnalyticsListener = Callable[[AnalyticsSnapshot], None]


class AnalyticsController:
    def __init__(
        self,
        repository: AnalyticsRepository,
        engine: AnalyticsEngine,
        snapshot_source: Callable[[], EventStoreSnapshot],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(repository, AnalyticsRepository):
            raise TypeError("repository must be AnalyticsRepository")
        if not isinstance(engine, AnalyticsEngine):
            raise TypeError("engine must be AnalyticsEngine")
        if not callable(snapshot_source):
            raise TypeError("snapshot_source must be callable")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable or None")
        self._repository = repository
        self._engine = engine
        self._snapshot_source = snapshot_source
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._snapshot = AnalyticsSnapshot.initial()
        self._listeners: dict[int, AnalyticsListener] = {}
        self._next_listener_id = 1
        self._closed = False
        self.refresh()

    def snapshot(self) -> AnalyticsSnapshot:
        with self._lock:
            return self._snapshot

    def refresh(
        self,
        *,
        symbol: str | None = None,
        strategy_version: str | None = None,
    ) -> AnalyticsSnapshot:
        with self._lock:
            self._ensure_open()
        try:
            dataset = self._repository.load(
                self._snapshot_source(),
                symbol=symbol,
                strategy_version=strategy_version,
            )
            result = self._engine.analyze(dataset)
            snapshot = AnalyticsSnapshot(
                status=(
                    AnalyticsStatus.READY
                    if dataset.trades
                    else AnalyticsStatus.EMPTY
                ),
                performance=result.performance,
                risk=result.risk,
                strategy=result.strategy,
                symbols=result.symbols,
                time_metrics=result.time_metrics,
                selected_symbol=(
                    symbol.upper() if symbol is not None else None
                ),
                selected_strategy=strategy_version,
                updated_at=self._clock(),
                errors=(),
            )
        except (TypeError, ValueError) as exc:
            current = self.snapshot()
            snapshot = AnalyticsSnapshot(
                AnalyticsStatus.ERROR,
                current.performance,
                current.risk,
                current.strategy,
                current.symbols,
                current.time_metrics,
                current.selected_symbol,
                current.selected_strategy,
                current.updated_at,
                (str(exc),),
            )
        with self._lock:
            self._snapshot = snapshot
        self._notify()
        return snapshot

    def filter(
        self,
        *,
        symbol: str | None = None,
        strategy_version: str | None = None,
    ) -> AnalyticsSnapshot:
        return self.refresh(
            symbol=symbol,
            strategy_version=strategy_version,
        )

    def aggregate(self) -> AnalyticsSnapshot:
        return self.refresh()

    def subscribe(self, listener: AnalyticsListener) -> int:
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
            self._closed = True
            self._listeners.clear()
            self._snapshot = AnalyticsSnapshot(
                AnalyticsStatus.CLOSED,
                self._snapshot.performance,
                self._snapshot.risk,
                self._snapshot.strategy,
                self._snapshot.symbols,
                self._snapshot.time_metrics,
                self._snapshot.selected_symbol,
                self._snapshot.selected_strategy,
                self._snapshot.updated_at,
                self._snapshot.errors,
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
            raise RuntimeError("analytics controller is closed")
