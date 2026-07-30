from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from threading import RLock
from typing import Protocol

from app.operations.runtime import PaperRuntimeEvent


class RuntimeEventSource(Protocol):
    """Read-only boundary for a recorded runtime event session."""

    def read_events(self) -> tuple[PaperRuntimeEvent, ...]: ...


@dataclass(frozen=True, slots=True)
class InMemoryRuntimeEventSource:
    """Immutable in-memory event source for replay and tests."""

    events: tuple[PaperRuntimeEvent, ...]

    def __init__(self, events: Iterable[PaperRuntimeEvent] = ()) -> None:
        snapshot = tuple(events)
        _validate_events(snapshot)
        object.__setattr__(self, "events", snapshot)

    def read_events(self) -> tuple[PaperRuntimeEvent, ...]:
        return self.events


class RuntimeEventRecorder:
    """Thread-safe in-memory runtime event sink and replay source."""

    def __init__(self) -> None:
        self._events: list[PaperRuntimeEvent] = []
        self._lock = RLock()

    def record(self, event: PaperRuntimeEvent) -> None:
        if not isinstance(event, PaperRuntimeEvent):
            raise TypeError("event must be a PaperRuntimeEvent")
        with self._lock:
            self._events.append(event)

    def __call__(self, event: PaperRuntimeEvent) -> None:
        self.record(event)

    def read_events(self) -> tuple[PaperRuntimeEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def snapshot(self) -> InMemoryRuntimeEventSource:
        return InMemoryRuntimeEventSource(self.read_events())

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


def _validate_events(events: tuple[PaperRuntimeEvent, ...]) -> None:
    if any(not isinstance(event, PaperRuntimeEvent) for event in events):
        raise TypeError("events must contain PaperRuntimeEvent instances")


__all__ = [
    "InMemoryRuntimeEventSource",
    "RuntimeEventRecorder",
    "RuntimeEventSource",
]
