"""Bounded off-thread evaluation and persistence with failure isolation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread
from typing import Callable

from .evaluator import EvaluationPolicy, evaluate_entry_opportunity
from .models import EntryOpportunityValueInput, EntryOpportunityValueObservation, ShadowAction
from .store import ObservationStore


@dataclass(frozen=True, slots=True)
class ShadowServiceMetrics:
    queue_depth: int
    queue_high_water: int
    observations_accepted: int
    observations_completed: int
    rejections: int
    failures: int
    outstanding: int
    maximum_worker_lag_ms: float
    classification_counts: tuple[tuple[ShadowAction, int], ...]
    accepting: bool
    stopped: bool


@dataclass(frozen=True, slots=True)
class _Work:
    context: EntryOpportunityValueInput
    enqueued_at: datetime


class EntryOpportunityValueService:
    """Research sidecar with no execution failure propagation or authority."""

    def __init__(
        self,
        store: ObservationStore,
        *,
        capacity: int = 1024,
        policy: EvaluationPolicy = EvaluationPolicy(),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        observation_sink: Callable[[EntryOpportunityValueObservation], None] | None = None,
        evaluator: Callable[..., EntryOpportunityValueObservation] = evaluate_entry_opportunity,
    ) -> None:
        if capacity <= 0:
            raise ValueError("shadow queue capacity must be positive")
        self._store = store
        self._queue: Queue[_Work] = Queue(maxsize=capacity)
        self._policy = policy
        self._clock = clock
        self._sink = observation_sink
        self._evaluator = evaluator
        self._lock = RLock()
        self._stop = Event()
        self._accepting = True
        self._stopped = False
        self._high_water = 0
        self._accepted = 0
        self._completed = 0
        self._rejections = 0
        self._failures = 0
        self._maximum_lag_ms = 0.0
        self._classifications: Counter[ShadowAction] = Counter()
        self._previous: dict[str, EntryOpportunityValueObservation] = {}
        self._thread = Thread(target=self._run, name="atlas-entry-value-research", daemon=True)
        self._thread.start()

    def observe(self, context: EntryOpportunityValueInput) -> bool:
        """Nonblocking admission; false means research-only loss under pressure."""

        if not isinstance(context, EntryOpportunityValueInput):
            with self._lock:
                self._rejections += 1
            return False
        with self._lock:
            if not self._accepting:
                self._rejections += 1
                return False
            try:
                self._queue.put_nowait(_Work(context=context, enqueued_at=self._clock()))
            except Full:
                self._rejections += 1
                return False
            self._accepted += 1
            self._high_water = max(self._high_water, self._queue.qsize())
            return True

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        if timeout_seconds < 0:
            raise ValueError("shutdown timeout cannot be negative")
        with self._lock:
            self._accepting = False
            self._stop.set()
        self._thread.join(timeout_seconds)
        stopped = not self._thread.is_alive()
        with self._lock:
            self._stopped = stopped
            if not stopped:
                self._failures += 1
        return stopped

    def metrics(self) -> ShadowServiceMetrics:
        with self._lock:
            return ShadowServiceMetrics(
                queue_depth=self._queue.qsize(),
                queue_high_water=self._high_water,
                observations_accepted=self._accepted,
                observations_completed=self._completed,
                rejections=self._rejections,
                failures=self._failures,
                outstanding=self._accepted - self._completed - self._failures,
                maximum_worker_lag_ms=self._maximum_lag_ms,
                classification_counts=tuple(
                    (action, self._classifications[action]) for action in ShadowAction
                ),
                accepting=self._accepting,
                stopped=self._stopped,
            )

    def _run(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                work = self._queue.get(timeout=0.05)
            except Empty:
                continue
            now = self._clock()
            lag_ms = max(0.0, (now - work.enqueued_at).total_seconds() * 1000.0)
            key = work.context.lifecycle_id
            try:
                observation = self._evaluator(
                    work.context,
                    evaluated_at=max(now, work.context.decision_cutoff),
                    previous=self._previous.get(key),
                    policy=self._policy,
                )
                self._store.append(observation)
                if self._sink is not None:
                    try:
                        self._sink(observation)
                    except Exception:
                        pass
                with self._lock:
                    self._previous[key] = observation
                    self._completed += 1
                    self._classifications[observation.shadow_action] += 1
            except Exception:
                # Research loss is counted and isolated; it never reaches the caller
                # and never changes any order or authorization state.
                with self._lock:
                    self._failures += 1
            finally:
                with self._lock:
                    self._maximum_lag_ms = max(self._maximum_lag_ms, lag_ms)
                self._queue.task_done()
        try:
            self._store.close()
        except Exception:
            with self._lock:
                self._failures += 1
        with self._lock:
            self._stopped = True


__all__ = ["EntryOpportunityValueService", "ShadowServiceMetrics"]
