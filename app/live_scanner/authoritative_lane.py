"""Bounded ordered delivery for authoritative market-event consumers."""

from __future__ import annotations

from collections import deque
from queue import Full, Queue
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any, Callable


class AuthoritativeLaneOverflow(RuntimeError):
    """The lossless lane cannot accept another event safely."""


class AuthoritativeEventLane:
    """Ordered, bounded event delivery with explicit fail-safe overflow."""

    def __init__(self, observer: Callable[[Any], object], *, capacity: int = 4096):
        if not callable(observer):
            raise TypeError("authoritative observer must be callable")
        if capacity <= 0:
            raise ValueError("authoritative lane capacity must be positive")
        self._observer = observer
        self.capacity = int(capacity)
        self._queue: Queue[tuple[float, Any] | None] = Queue(maxsize=self.capacity)
        self._lock = Lock()
        self._stop = Event()
        self._started = False
        self._accepting = True
        self._thread: Thread | None = None
        self._depth = 0
        self._high_water = 0
        self._enqueued = 0
        self._dequeued = 0
        self._overflow = 0
        self._failure: BaseException | None = None
        self._ages_ms: deque[float] = deque(maxlen=2048)

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._thread = Thread(
                target=self._run,
                name="atlas-authoritative-market-events",
                daemon=True,
            )
            self._thread.start()

    def publish(self, event: Any) -> None:
        with self._lock:
            if self._failure is not None:
                raise RuntimeError("authoritative lane worker failed") from self._failure
            if not self._accepting:
                raise RuntimeError("authoritative lane is stopped")
        item = (monotonic(), event)
        with self._lock:
            if self._depth >= self.capacity:
                self._overflow += 1
                raise AuthoritativeLaneOverflow(
                    "authoritative market-event lane capacity exhausted"
                )
            try:
                self._queue.put_nowait(item)
            except Full as exc:
                self._overflow += 1
                raise AuthoritativeLaneOverflow(
                    "authoritative market-event lane capacity exhausted"
                ) from exc
            self._depth += 1
            self._high_water = max(self._high_water, self._depth)
            self._enqueued += 1

    def stop(self, *, timeout: float = 5.0) -> None:
        with self._lock:
            self._accepting = False
            started = self._started
        if not started:
            return
        with self._lock:
            failed = self._failure is not None
        if failed:
            # The worker has stopped at the failing event.  Account for the
            # unreconciled tail explicitly so shutdown cannot hang forever;
            # the failure remains visible in metrics and the caller receives
            # a fail-safe error from the next publish.
            while True:
                try:
                    self._queue.get_nowait()
                except Exception:
                    break
                else:
                    self._queue.task_done()
        self._queue.join()
        self._stop.set()
        self._queue.put(None)
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                raise RuntimeError("authoritative lane shutdown timed out")

    def metrics(self) -> dict[str, object]:
        with self._lock:
            ages = sorted(self._ages_ms)
            return {
                "authoritative_lane_depth": self._depth,
                "authoritative_lane_high_water": self._high_water,
                "authoritative_lane_capacity": self.capacity,
                "authoritative_events_enqueued": self._enqueued,
                "authoritative_events_dequeued": self._dequeued,
                "authoritative_lane_overflow": self._overflow,
                "authoritative_oldest_age_ms": max(ages, default=0.0),
                "authoritative_lane_failure": (
                    None if self._failure is None else type(self._failure).__name__
                ),
            }

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            started, event = item
            try:
                self._observer(event)
                age_ms = max(0.0, (monotonic() - started) * 1000.0)
                with self._lock:
                    self._ages_ms.append(age_ms)
            except BaseException as exc:
                with self._lock:
                    self._failure = exc
                    self._accepting = False
                # Fail-safe: stop consuming later authoritative events rather
                # than silently skipping them after an observer failure.
                self._queue.task_done()
                return
            finally:
                with self._lock:
                    self._depth = max(0, self._depth - 1)
                    self._dequeued += 1
                self._queue.task_done()


__all__ = ["AuthoritativeEventLane", "AuthoritativeLaneOverflow"]
