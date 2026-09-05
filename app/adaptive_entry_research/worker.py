"""Bounded off-thread evaluation with complete producer failure isolation."""

from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from typing import Callable

from .contracts import MaterialChangeReason, ShadowRecommendation, WorkingEntrySnapshot
from .evaluator import evaluate_reassessment
from .material_change import semantic_signature
from .outcomes import BoundedOutcomeTracker
from .persistence import ResearchStore


@dataclass(frozen=True, slots=True)
class WorkerMetrics:
    accepted: int
    completed: int
    rejected: int
    failed: int
    queue_depth: int
    queue_high_water: int
    retained_orders: int
    accepting: bool
    stopped: bool
    classifications: tuple[tuple[str, int], ...]
    outcome_points_accepted: int
    outcome_labels_completed: int
    outcome_points_suppressed: int
    retained_outcome_signatures: int
    admission_contention_drops: int
    semantic_repeats_suppressed: int = 0
    duplicate_recommendations_suppressed: int = 0


class AdaptiveEntryResearchWorker:
    def __init__(self, store: ResearchStore, *, outcome_store: ResearchStore | None = None,
                 capacity: int = 512,
                 evaluator: Callable[..., object] = evaluate_reassessment,
                 state_limit: int = 4096,
                 outcome_tracker: BoundedOutcomeTracker | None = None) -> None:
        if capacity <= 0 or state_limit <= 0:
            raise ValueError("worker bounds must be positive")
        self._store, self._outcome_store, self._evaluator = store, outcome_store or store, evaluator
        self._queue: Queue[tuple[object, ...]] = Queue(maxsize=capacity)
        self._stop, self._lock = Event(), Lock()
        self._accepted = self._completed = self._rejected = self._failed = self._high_water = 0
        self._accepting, self._stopped = True, False
        self._recent: OrderedDict[str, None] = OrderedDict()
        self._state_limit = state_limit
        self._classes: Counter[ShadowRecommendation] = Counter()
        self._outcomes = outcome_tracker or BoundedOutcomeTracker()
        self._outcome_accepted = self._outcome_completed = self._outcome_suppressed = 0
        self._contention_drops = 0
        self._outcome_signatures: OrderedDict[str, tuple[object, ...]] = OrderedDict()
        self._admission_keys: OrderedDict[tuple[object, ...], None] = OrderedDict()
        self._persisted_ids: OrderedDict[str, None] = OrderedDict()
        self._persisted_semantics: OrderedDict[tuple[object, ...], None] = OrderedDict()
        self._semantic_repeats_suppressed = 0
        self._duplicate_recommendations_suppressed = 0
        self._thread = Thread(target=self._run, name="atlas-adaptive-entry-research", daemon=True)
        self._thread.start()

    def observe(self, snapshot: WorkingEntrySnapshot, reasons: tuple[MaterialChangeReason, ...]) -> bool:
        if not self._lock.acquire(blocking=False):
            self._contention_drops += 1
            self._rejected += 1
            return False
        try:
            if not self._accepting:
                self._rejected += 1
                return False
            admission_key = (snapshot.order_id, snapshot.decision_cutoff,
                             semantic_signature(snapshot, reasons))
            if admission_key in self._admission_keys:
                self._semantic_repeats_suppressed += 1
                return False
            try:
                self._queue.put_nowait(("RECOMMENDATION", snapshot, reasons))
            except Full:
                self._rejected += 1
                return False
            self._accepted += 1
            self._admission_keys[admission_key] = None
            self._admission_keys.move_to_end(admission_key)
            while len(self._admission_keys) > self._state_limit:
                self._admission_keys.popitem(last=False)
            self._high_water = max(self._high_water, self._queue.qsize())
            return True
        finally:
            self._lock.release()

    def observe_market(self, *, symbol: str, observed_at: datetime, price: Decimal,
                       high: Decimal | None = None, low: Decimal | None = None) -> bool:
        """Admit future label evidence without exposing it to evaluation."""

        normalized = symbol.strip().upper()
        signature = (observed_at, price, high, low)
        if not self._lock.acquire(blocking=False):
            self._contention_drops += 1
            self._rejected += 1
            return False
        try:
            if self._outcome_signatures.get(normalized) == signature:
                self._outcome_suppressed += 1
                return False
            if not self._accepting:
                self._rejected += 1
                return False
            try:
                self._queue.put_nowait(("OUTCOME", normalized, observed_at, price, high, low))
            except Full:
                self._rejected += 1
                return False
            self._outcome_accepted += 1
            self._high_water = max(self._high_water, self._queue.qsize())
            self._outcome_signatures[normalized] = signature
            self._outcome_signatures.move_to_end(normalized)
            while len(self._outcome_signatures) > self._state_limit:
                self._outcome_signatures.popitem(last=False)
            return True
        finally:
            self._lock.release()

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        with self._lock:
            self._accepting = False
            self._stop.set()
        self._thread.join(timeout_seconds)
        stopped = not self._thread.is_alive()
        with self._lock:
            self._stopped = stopped
            if not stopped:
                self._failed += 1
        return stopped

    def metrics(self) -> WorkerMetrics:
        with self._lock:
            return WorkerMetrics(self._accepted, self._completed, self._rejected, self._failed,
                                 self._queue.qsize(), self._high_water, len(self._recent),
                                 self._accepting, self._stopped,
                                 tuple((item.value, self._classes[item]) for item in ShadowRecommendation),
                                 self._outcome_accepted, self._outcome_completed,
                                 self._outcome_suppressed, len(self._outcome_signatures),
                                 self._contention_drops, self._semantic_repeats_suppressed,
                                 self._duplicate_recommendations_suppressed)

    def _run(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                work = self._queue.get(timeout=0.05)
            except Empty:
                continue
            try:
                if work[0] == "RECOMMENDATION":
                    _, snapshot, reasons = work
                    result = self._evaluator(snapshot, reasons)
                    plan = result.fresh_hypothetical
                    semantic_key = (snapshot.order_id, result.recommendation.value,
                                    semantic_signature(snapshot, reasons), plan.entry,
                                    plan.stop, plan.quantity, plan.total_risk)
                    with self._lock:
                        if result.recommendation_id in self._persisted_ids or semantic_key in self._persisted_semantics:
                            self._duplicate_recommendations_suppressed += 1
                            continue
                    self._store.append(result)
                    self._outcomes.track(result)
                    with self._lock:
                        self._persisted_ids[result.recommendation_id] = None
                        self._persisted_semantics[semantic_key] = None
                        while len(self._persisted_ids) > self._state_limit:
                            self._persisted_ids.popitem(last=False)
                        while len(self._persisted_semantics) > self._state_limit:
                            self._persisted_semantics.popitem(last=False)
                        self._completed += 1
                        self._classes[result.recommendation] += 1
                        self._recent[snapshot.order_id] = None
                        self._recent.move_to_end(snapshot.order_id)
                        while len(self._recent) > self._state_limit:
                            self._recent.popitem(last=False)
                else:
                    _, symbol, observed_at, price, high, low = work
                    labels = self._outcomes.observe(
                        symbol=symbol, observed_at=observed_at, price=price,
                        high=high, low=low,
                    )
                    for label in labels:
                        self._outcome_store.append(label)
                    with self._lock:
                        self._outcome_completed += len(labels)
            except Exception:
                with self._lock:
                    self._failed += 1
            finally:
                self._queue.task_done()
        stores = (
            (self._store,)
            if self._outcome_store is self._store
            else (self._store, self._outcome_store)
        )
        for store in stores:
            try:
                store.close()
            except Exception:
                with self._lock:
                    self._failed += 1
        with self._lock:
            self._stopped = True


__all__ = ["AdaptiveEntryResearchWorker", "WorkerMetrics"]
