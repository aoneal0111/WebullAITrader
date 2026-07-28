from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from threading import RLock

from .archive import ReplayEventArchive
from .clock import ReplayClock
from .engine import ReplayEngine
from .models import (
    ReplayPosition,
    ReplaySnapshot,
    ReplaySpeed,
    ReplayState,
)


ReplayListener = Callable[[ReplaySnapshot], None]


class ReplayController:
    """Coordinate replay commands and expose immutable presentation state."""

    def __init__(
        self,
        archive: ReplayEventArchive,
        clock: ReplayClock,
        engine: ReplayEngine,
    ) -> None:
        if not isinstance(archive, ReplayEventArchive):
            raise TypeError("archive must be a ReplayEventArchive")
        if not isinstance(clock, ReplayClock):
            raise TypeError("clock must be a ReplayClock")
        if not isinstance(engine, ReplayEngine):
            raise TypeError("engine must be a ReplayEngine")
        self._lock = RLock()
        self._archive = archive
        self._clock = clock
        self._engine = engine
        self._session = None
        self._listeners: dict[int, ReplayListener] = {}
        self._next_listener_id = 1
        self._closed = False
        if archive.entries:
            self._engine.load(archive)
            self._session = archive.session("replay-session")

    def load(
        self,
        archive: ReplayEventArchive,
        *,
        session_id: str = "replay-session",
    ) -> None:
        with self._lock:
            self._ensure_open()
            self._engine.load(archive)
            self._archive = archive
            self._session = archive.session(session_id)
        self._notify()

    def play(self) -> None:
        self._command(self._engine.play)

    def pause(self) -> None:
        self._command(self._engine.pause)

    def resume(self) -> None:
        self._command(self._engine.resume)

    def stop(self) -> None:
        self._command(self._engine.stop)

    def step_forward(self) -> None:
        self._command(self._engine.step_forward)

    def step_backward(self) -> None:
        self._command(self._engine.step_backward)

    def seek(self, event_index: int) -> None:
        self._command(lambda: self._engine.seek(event_index))

    def set_speed(self, speed: ReplaySpeed) -> None:
        self._command(lambda: self._engine.set_speed(speed))

    def advance(self, elapsed_seconds: Decimal) -> int:
        with self._lock:
            self._ensure_open()
            published = self._engine.advance(elapsed_seconds)
        self._notify()
        return published

    def snapshot(self) -> ReplaySnapshot:
        with self._lock:
            if self._session is None:
                return ReplaySnapshot.initial()
            index = self._engine.event_index
            entry = (
                None
                if index == 0
                else self._archive.entries[index - 1]
            )
            total = len(self._archive.entries)
            progress = (
                Decimal("0")
                if total == 0
                else (
                    Decimal(index)
                    / Decimal(total)
                    * Decimal("100")
                )
            )
            return ReplaySnapshot(
                session=self._session,
                state=ReplayState.REPLAY,
                status=self._engine.status,
                position=ReplayPosition(
                    event_index=index,
                    total_events=total,
                    sequence_number=(
                        None
                        if entry is None
                        else entry.sequence_number
                    ),
                    timestamp=(
                        None if entry is None else entry.timestamp
                    ),
                    progress=progress,
                ),
                speed=self._clock.speed,
            )

    def subscribe(self, listener: ReplayListener) -> int:
        if not callable(listener):
            raise TypeError("listener must be callable")
        with self._lock:
            self._ensure_open()
            listener_id = self._next_listener_id
            self._next_listener_id += 1
            self._listeners[listener_id] = listener
            snapshot = self.snapshot()
        listener(snapshot)
        return listener_id

    def unsubscribe(self, listener_id: int) -> bool:
        with self._lock:
            return self._listeners.pop(listener_id, None) is not None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._engine.close()
            self._listeners.clear()
            self._closed = True

    def _command(self, command: Callable[[], object]) -> None:
        with self._lock:
            self._ensure_open()
            command()
        self._notify()

    def _notify(self) -> None:
        with self._lock:
            snapshot = self.snapshot()
            listeners = tuple(self._listeners.values())
        for listener in listeners:
            listener(snapshot)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("replay controller is closed")
