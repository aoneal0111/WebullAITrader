"""Bounded, latest-state handoff for non-authoritative projections."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Condition, Event, Thread
from time import monotonic
from typing import Callable


@dataclass(frozen=True, slots=True)
class ProjectionHandoffMetrics:
    submitted: int = 0
    processed: int = 0
    coalesced: int = 0
    dropped: int = 0
    pending: int = 0
    high_water: int = 0
    processing_latency_ms: float = 0.0


class BoundedProjectionHandoff:
    """Non-blocking, per-key latest-state handoff.

    This is intentionally suitable only for projection/research work.  The
    authoritative scanner and Warrior execution path must remain synchronous.
    """

    def __init__(
        self,
        handler: Callable[[object], object],
        *,
        maximum_keys: int = 512,
        autostart: bool = False,
    ) -> None:
        if not callable(handler):
            raise TypeError("projection handler must be callable")
        if maximum_keys <= 0:
            raise ValueError("maximum_keys must be positive")
        self._handler = handler
        self._maximum_keys = maximum_keys
        self._pending: dict[str, object] = {}
        self._condition = Condition()
        self._stop = Event()
        self._thread: Thread | None = None
        self._running = False
        self._submitted = 0
        self._processed = 0
        self._coalesced = 0
        self._dropped = 0
        self._high_water = 0
        self._latency_total_ms = 0.0

        if autostart:
            self.start()

    def start(self, *, worker: bool = True) -> None:
        with self._condition:
            if self._running:
                return
            self._stop.clear()
            self._running = True
            if worker:
                self._thread = Thread(
                    target=self._run,
                    name="atlas-projection-handoff",
                    daemon=True,
                )
                self._thread.start()

    def submit(self, key: str, value: object) -> bool:
        """Publish without waiting; latest value wins for each symbol."""
        normalized = str(key).strip().upper()
        if not normalized:
            return False
        with self._condition:
            if not self._running:
                self._dropped += 1
                return False
            self._submitted += 1
            if normalized in self._pending:
                self._coalesced += 1
            elif len(self._pending) >= self._maximum_keys:
                self._dropped += 1
                return False
            self._pending[normalized] = value
            self._high_water = max(self._high_water, len(self._pending))
            self._condition.notify()
            return True

    def drain_once(self) -> bool:
        with self._condition:
            if not self._pending:
                return False
            _key, value = self._pending.popitem()
        started = monotonic()
        try:
            self._handler(value)
        except Exception:
            # Projection failures cannot affect market-event processing.
            pass
        finally:
            with self._condition:
                self._processed += 1
                self._latency_total_ms += (monotonic() - started) * 1000.0
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

    def stop(self, *, drain: bool = False, timeout_seconds: float = 2.0) -> None:
        with self._condition:
            self._stop.set()
            self._condition.notify_all()
            thread = self._thread
        if not drain:
            with self._condition:
                self._pending.clear()
        if thread is not None:
            thread.join(timeout_seconds)
        with self._condition:
            self._running = False
            self._thread = None

    def metrics(self) -> ProjectionHandoffMetrics:
        with self._condition:
            processed = self._processed
            average = (
                self._latency_total_ms / processed if processed else 0.0
            )
            return ProjectionHandoffMetrics(
                submitted=self._submitted,
                processed=processed,
                coalesced=self._coalesced,
                dropped=self._dropped,
                pending=len(self._pending),
                high_water=self._high_water,
                processing_latency_ms=average,
            )

    def memory_metrics(self) -> dict[str, int | float]:
        metrics = self.metrics()
        return {
            "submitted": metrics.submitted,
            "processed": metrics.processed,
            "coalesced": metrics.coalesced,
            "dropped": metrics.dropped,
            "pending": metrics.pending,
            "high_water": metrics.high_water,
        }

    def __enter__(self) -> "BoundedProjectionHandoff":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


__all__ = ["BoundedProjectionHandoff", "ProjectionHandoffMetrics"]
