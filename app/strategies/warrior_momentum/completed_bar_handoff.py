"""Bounded, ordered delivery of completed bars to research consumers."""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from threading import Condition, Event, Thread
from time import monotonic
from typing import Callable

from .models import MinuteBar


class CompletedBarSubmitResult(StrEnum):
    ACCEPTED = "ACCEPTED"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class CompletedBarResearchWork:
    symbol: str
    session: str
    revision: int
    bar: MinuteBar
    shadow_evaluation_ids: tuple[str, ...] = ()

    @property
    def identity(self) -> tuple[object, ...]:
        return (
            self.symbol,
            self.session,
            self.bar.timestamp,
            self.bar.open,
            self.bar.high,
            self.bar.low,
            self.bar.close,
            self.bar.volume,
        )


@dataclass(frozen=True, slots=True)
class CompletedBarResearchMetrics:
    accepted: int = 0
    completed: int = 0
    duplicate_suppressed: int = 0
    rejected: int = 0
    failures: int = 0
    depth: int = 0
    high_water: int = 0


class BoundedCompletedBarResearchHandoff:
    """One FIFO worker; exact duplicates coalesce, distinct bars never do."""

    def __init__(
        self,
        handler: Callable[[CompletedBarResearchWork], None],
        *,
        capacity: int = 4096,
        completed_identity_capacity: int = 8192,
        autostart: bool = True,
    ) -> None:
        if not callable(handler):
            raise TypeError("completed-bar research handler must be callable")
        if capacity <= 0 or completed_identity_capacity <= 0:
            raise ValueError("completed-bar research capacities must be positive")
        self._handler = handler
        self._capacity = int(capacity)
        self._completed_identity_capacity = int(completed_identity_capacity)
        self._condition = Condition()
        self._stop = Event()
        self._pending: deque[CompletedBarResearchWork] = deque()
        self._pending_identities: set[tuple[object, ...]] = set()
        self._completed_identities: OrderedDict[tuple[object, ...], None] = OrderedDict()
        self._last_timestamp_by_symbol: dict[str, datetime] = {}
        self._thread: Thread | None = None
        self._accepting = True
        self._active = False
        self._accepted = 0
        self._completed = 0
        self._duplicate_suppressed = 0
        self._rejected = 0
        self._failures = 0
        self._high_water = 0
        if autostart:
            self.start()

    def start(self) -> None:
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._accepting = True
            self._thread = Thread(
                target=self._run,
                name="warrior-completed-bar-research",
                daemon=True,
            )
            self._thread.start()

    def submit(self, work: CompletedBarResearchWork) -> CompletedBarSubmitResult:
        identity = work.identity
        with self._condition:
            if not self._accepting:
                self._rejected += 1
                return CompletedBarSubmitResult.REJECTED
            if identity in self._pending_identities or identity in self._completed_identities:
                self._duplicate_suppressed += 1
                return CompletedBarSubmitResult.DUPLICATE
            last_timestamp = self._last_timestamp_by_symbol.get(work.symbol)
            if last_timestamp is not None and work.bar.timestamp < last_timestamp:
                self._rejected += 1
                return CompletedBarSubmitResult.REJECTED
            if len(self._pending) >= self._capacity:
                self._rejected += 1
                return CompletedBarSubmitResult.REJECTED
            self._pending.append(work)
            self._pending_identities.add(identity)
            self._last_timestamp_by_symbol[work.symbol] = work.bar.timestamp
            self._accepted += 1
            self._high_water = max(self._high_water, len(self._pending))
            self._condition.notify()
            return CompletedBarSubmitResult.ACCEPTED

    def wait_idle(self, timeout_seconds: float = 5.0) -> bool:
        deadline = monotonic() + max(0.0, timeout_seconds)
        with self._condition:
            while self._pending or self._active:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)
            return True

    def stop(self, *, drain: bool = True, timeout_seconds: float = 5.0) -> bool:
        with self._condition:
            self._accepting = False
            if not drain:
                self._rejected += len(self._pending)
                self._pending.clear()
                self._pending_identities.clear()
            self._stop.set()
            self._condition.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(max(0.0, timeout_seconds))
        with self._condition:
            stopped = thread is None or not thread.is_alive()
            if stopped:
                self._thread = None
            return stopped

    def metrics(self) -> CompletedBarResearchMetrics:
        with self._condition:
            return CompletedBarResearchMetrics(
                accepted=self._accepted,
                completed=self._completed,
                duplicate_suppressed=self._duplicate_suppressed,
                rejected=self._rejected,
                failures=self._failures,
                depth=len(self._pending) + int(self._active),
                high_water=self._high_water,
            )

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._pending and not self._stop.is_set():
                    self._condition.wait(timeout=0.25)
                if self._stop.is_set() and not self._pending:
                    self._condition.notify_all()
                    return
                work = self._pending.popleft()
                self._pending_identities.discard(work.identity)
                self._active = True
            failed = False
            try:
                self._handler(work)
            except Exception:
                failed = True
            finally:
                with self._condition:
                    self._active = False
                    self._completed += 1
                    if failed:
                        self._failures += 1
                    self._completed_identities[work.identity] = None
                    self._completed_identities.move_to_end(work.identity)
                    while len(self._completed_identities) > self._completed_identity_capacity:
                        self._completed_identities.popitem(last=False)
                    self._condition.notify_all()


__all__ = [
    "BoundedCompletedBarResearchHandoff",
    "CompletedBarResearchMetrics",
    "CompletedBarResearchWork",
    "CompletedBarSubmitResult",
]
