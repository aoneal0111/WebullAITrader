"""Bounded off-thread dynamic-discovery research service."""

from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread
from time import perf_counter
from typing import Callable

from .evaluator import (
    DynamicDiscoveryPolicy,
    evaluate_dynamic_momentum,
    semantic_signature,
)
from .models import BroadMarketSnapshot, DynamicMomentumObservation, MomentumEvent
from .store import DiscoveryStore


@dataclass(frozen=True, slots=True)
class DynamicDiscoveryMetrics:
    enabled: bool
    accepted: int
    completed: int
    suppressed: int
    rejected: int
    failed: int
    evaluation_failures: int
    persistence_failures: int
    outstanding: int
    queue_depth: int
    queue_high_water: int
    retained_symbols: int
    maximum_producer_latency_ms: float
    maximum_worker_lag_ms: float
    promotion_count: int
    event_counts: tuple[tuple[MomentumEvent, int], ...]
    accepting: bool
    stopped: bool


@dataclass(frozen=True, slots=True)
class _Work:
    snapshot: BroadMarketSnapshot
    enqueued_at: datetime


class DynamicMomentumDiscoveryService:
    """Research results have no return/control path into production."""

    def __init__(
        self, store: DiscoveryStore, *, enabled: bool = True, capacity: int = 1024,
        maximum_retained_symbols: int = 1000,
        policy: DynamicDiscoveryPolicy = DynamicDiscoveryPolicy(),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        timer: Callable[[], float] = perf_counter,
        evaluator: Callable[..., DynamicMomentumObservation] = evaluate_dynamic_momentum,
        observation_sink: Callable[[DynamicMomentumObservation], None] | None = None,
    ) -> None:
        if capacity <= 0 or maximum_retained_symbols <= 0:
            raise ValueError("research bounds must be positive")
        self.enabled = bool(enabled)
        self._store = store
        self._capacity = capacity
        self._maximum_retained_symbols = maximum_retained_symbols
        self._policy = policy
        self._clock = clock
        self._timer = timer
        self._evaluator = evaluator
        self._sink = observation_sink
        self._queue: Queue[_Work] | None = None
        self._stop = Event()
        self._lock = RLock()
        self._thread: Thread | None = None
        self._accepting = self.enabled
        self._stopped = not self.enabled
        self._signatures: OrderedDict[str, str] = OrderedDict()
        self._previous: OrderedDict[str, BroadMarketSnapshot] = OrderedDict()
        self._accepted = self._completed = self._suppressed = 0
        self._rejected = self._failed = self._high_water = 0
        self._evaluation_failures = self._persistence_failures = 0
        self._max_producer_ms = self._max_lag_ms = 0.0
        self._promotions = 0
        self._events: Counter[MomentumEvent] = Counter()
        if self.enabled:
            self._queue = Queue(maxsize=capacity)
            self._thread = Thread(
                target=self._run, name="atlas-dynamic-momentum-research", daemon=True,
            )
            self._thread.start()
            self._stopped = False

    def observe(self, snapshot: BroadMarketSnapshot) -> bool:
        started = self._timer()
        try:
            if not isinstance(snapshot, BroadMarketSnapshot):
                with self._lock:
                    self._rejected += 1
                return False
            signature = semantic_signature(snapshot)
            with self._lock:
                if not self._accepting or self._queue is None:
                    self._rejected += 1
                    return False
                if self._signatures.get(snapshot.symbol) == signature:
                    self._suppressed += 1
                    return False
                try:
                    self._queue.put_nowait(_Work(snapshot, self._clock()))
                except Full:
                    self._rejected += 1
                    return False
                self._signatures[snapshot.symbol] = signature
                self._signatures.move_to_end(snapshot.symbol)
                while len(self._signatures) > self._maximum_retained_symbols:
                    self._signatures.popitem(last=False)
                self._accepted += 1
                self._high_water = max(self._high_water, self._queue.qsize())
                return True
        except Exception:
            with self._lock:
                self._failed += 1
            return False
        finally:
            elapsed = max(0.0, (self._timer() - started) * 1000.0)
            with self._lock:
                self._max_producer_ms = max(self._max_producer_ms, elapsed)

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        with self._lock:
            self._accepting = False
            self._stop.set()
        if self._thread is None:
            self._stopped = True
            return True
        self._thread.join(max(0.0, timeout_seconds))
        stopped = not self._thread.is_alive()
        with self._lock:
            self._stopped = stopped
            if not stopped:
                self._failed += 1
        return stopped

    def metrics(self) -> DynamicDiscoveryMetrics:
        with self._lock:
            depth = self._queue.qsize() if self._queue is not None else 0
            return DynamicDiscoveryMetrics(
                enabled=self.enabled, accepted=self._accepted,
                completed=self._completed, suppressed=self._suppressed,
                rejected=self._rejected, failed=self._failed,
                evaluation_failures=self._evaluation_failures,
                persistence_failures=self._persistence_failures,
                outstanding=self._accepted - self._completed,
                queue_depth=depth, queue_high_water=self._high_water,
                retained_symbols=len(self._previous),
                maximum_producer_latency_ms=self._max_producer_ms,
                maximum_worker_lag_ms=self._max_lag_ms,
                promotion_count=self._promotions,
                event_counts=tuple((event, self._events[event]) for event in MomentumEvent),
                accepting=self._accepting, stopped=self._stopped,
            )

    def estimated_retained_bytes(self) -> int:
        """Conservative structural estimate; excludes interpreter allocator overhead."""
        with self._lock:
            return (
                len(self._previous) * 2048
                + len(self._signatures) * 256
                + (self._queue.qsize() if self._queue is not None else 0) * 2048
            )

    def _run(self) -> None:
        assert self._queue is not None
        while not self._stop.is_set() or not self._queue.empty():
            try:
                work = self._queue.get(timeout=0.05)
            except Empty:
                continue
            now = self._clock()
            lag = max(0.0, (now - work.enqueued_at).total_seconds() * 1000)
            try:
                try:
                    previous = self._previous.get(work.snapshot.symbol)
                    observation = self._evaluator(
                        work.snapshot, previous=previous,
                        evaluated_at=max(now, work.snapshot.decision_cutoff),
                        policy=self._policy,
                    )
                except Exception:
                    with self._lock:
                        self._evaluation_failures += 1
                        self._failed += 1
                        self._completed += 1
                    observation = None
                if observation is not None:
                    persisted = True
                    try:
                        self._store.append(observation)
                    except Exception:
                        persisted = False
                        with self._lock:
                            self._persistence_failures += 1
                            self._failed += 1
                    if self._sink is not None:
                        try:
                            self._sink(observation)
                        except Exception:
                            pass
                    with self._lock:
                        self._previous[work.snapshot.symbol] = work.snapshot
                        self._previous.move_to_end(work.snapshot.symbol)
                        while len(self._previous) > self._maximum_retained_symbols:
                            self._previous.popitem(last=False)
                        self._completed += 1
                        if persisted:
                            self._promotions += int(
                                observation.shadow_promote_to_full_analysis
                            )
                            self._events.update(observation.events)
            finally:
                with self._lock:
                    self._max_lag_ms = max(self._max_lag_ms, lag)
                self._queue.task_done()
        try:
            self._store.close()
        except Exception:
            with self._lock:
                self._persistence_failures += 1
                self._failed += 1
        with self._lock:
            self._stopped = True


__all__ = ["DynamicDiscoveryMetrics", "DynamicMomentumDiscoveryService"]
