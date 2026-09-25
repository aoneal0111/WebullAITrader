"""Bounded latest-state handoff for Warrior decision intelligence.

The worker owns research/taxonomy computation only.  Published results are
immutable and generation checked so a completion for superseded input can
never become the current treatment authority.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Condition, Event, Thread
from time import monotonic
from typing import Callable


@dataclass(frozen=True, slots=True)
class IntelligenceWork:
    key: str
    generation: int
    identity: object
    payload: object


@dataclass(frozen=True, slots=True)
class IntelligencePublication:
    key: str
    generation: int
    identity: object
    value: object


@dataclass(frozen=True, slots=True)
class IntelligenceWorkerMetrics:
    accepted: int = 0
    completed: int = 0
    superseded: int = 0
    rejected: int = 0
    failures: int = 0
    depth: int = 0
    high_water: int = 0


class BoundedIntelligenceHandoff:
    """One-worker, per-key coalescing mailbox with version-safe publication."""

    def __init__(self, handler: Callable[[object], object], *,
                 maximum_keys: int = 128, autostart: bool = False) -> None:
        if not callable(handler):
            raise TypeError("intelligence handler must be callable")
        if maximum_keys <= 0:
            raise ValueError("maximum_keys must be positive")
        self._handler = handler
        self._maximum_keys = maximum_keys
        self._condition = Condition()
        self._stop = Event()
        self._thread: Thread | None = None
        self._running = False
        self._pending: dict[str, IntelligenceWork] = {}
        self._published: OrderedDict[str, IntelligencePublication] = OrderedDict()
        self._generations: dict[str, int] = {}
        self._active = 0
        self._accepted = 0
        self._completed = 0
        self._superseded = 0
        self._rejected = 0
        self._failures = 0
        self._high_water = 0
        if autostart:
            self.start()

    def start(self) -> None:
        with self._condition:
            if self._running:
                return
            self._stop.clear()
            self._running = True
            self._thread = Thread(
                target=self._run,
                name="atlas-warrior-intelligence",
                daemon=True,
            )
            self._thread.start()

    def submit(self, key: str, identity: object, payload: object) -> bool:
        normalized = str(key).strip().upper()
        if not normalized:
            return False
        with self._condition:
            if not self._running or self._stop.is_set():
                self._rejected += 1
                return False
            if normalized not in self._pending and len(self._pending) >= self._maximum_keys:
                self._rejected += 1
                return False
            generation = self._generations.get(normalized, 0) + 1
            self._generations[normalized] = generation
            if normalized in self._pending:
                self._superseded += 1
            self._pending[normalized] = IntelligenceWork(
                normalized, generation, identity, payload,
            )
            self._accepted += 1
            self._high_water = max(self._high_water, len(self._pending))
            self._condition.notify()
            return True

    def lookup(self, key: str) -> IntelligencePublication | None:
        normalized = str(key).strip().upper()
        with self._condition:
            publication = self._published.get(normalized)
            if publication is not None:
                self._published.move_to_end(normalized)
            return publication

    def drain_once(self) -> bool:
        with self._condition:
            if not self._pending:
                return False
            key, work = self._pending.popitem()
            self._active += 1
        try:
            value = self._handler(work.payload)
        except Exception:
            with self._condition:
                self._failures += 1
                self._active -= 1
                self._condition.notify_all()
            return True
        with self._condition:
            self._completed += 1
            self._active -= 1
            if self._generations.get(key) != work.generation:
                self._superseded += 1
                self._condition.notify_all()
                return True
            self._published[key] = IntelligencePublication(
                key, work.generation, work.identity, value,
            )
            self._published.move_to_end(key)
            while len(self._published) > self._maximum_keys:
                evicted, _publication = self._published.popitem(last=False)
                if evicted not in self._pending:
                    self._generations.pop(evicted, None)
            self._condition.notify_all()
        return True

    def wait_idle(self, timeout_seconds: float = 5.0) -> bool:
        deadline = monotonic() + max(0.0, timeout_seconds)
        with self._condition:
            while self._pending or self._active:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)
            return True

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._pending and not self._stop.is_set():
                    self._condition.wait(timeout=0.25)
                if self._stop.is_set() and not self._pending:
                    self._running = False
                    return
            self.drain_once()

    def stop(self, *, drain: bool = True, timeout_seconds: float = 5.0) -> bool:
        started = monotonic()
        with self._condition:
            self._stop.set()
            if not drain:
                self._superseded += len(self._pending)
                self._pending.clear()
            self._condition.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(max(0.0, timeout_seconds))
        elapsed = monotonic() - started
        with self._condition:
            stopped = thread is None or not thread.is_alive()
            if stopped:
                self._running = False
                self._thread = None
            return stopped and elapsed <= max(0.0, timeout_seconds) + 0.01

    def metrics(self) -> IntelligenceWorkerMetrics:
        with self._condition:
            return IntelligenceWorkerMetrics(
                accepted=self._accepted,
                completed=self._completed,
                superseded=self._superseded,
                rejected=self._rejected,
                failures=self._failures,
                depth=len(self._pending),
                high_water=self._high_water,
            )


__all__ = [
    "BoundedIntelligenceHandoff", "IntelligencePublication",
    "IntelligenceWorkerMetrics", "IntelligenceWork",
]
