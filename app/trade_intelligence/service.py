"""Bounded nonblocking producer boundary and isolated research worker."""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread
from typing import Callable

from .experience_store import (
    ExperienceStore, _bar_from_json, _decision_from_json, _experience_from_json,
    _paper_observation_from_json,
)
from .discovery_runtime import (
    DiscoveryTelemetry, RuntimeDiscoveryObservation, discovery_observation_from_dict,
    discovery_observation_payload,
)
from .discovery_worker import DiscoveryWorker
from .models import (
    DecisionObservation, HORIZONS_MINUTES, MissedOpportunityClassification,
    PaperExecutionObservation, PriceBar, TradeOpportunityExperience, WorkerMetrics,
    canonical_json, experience_payload,
)
from .outcome_engine import OutcomeEngine, classify_missed_opportunity

MAX_SERIALIZED_WORK_BYTES = 64 * 1024
DEFAULT_STORE_PATH = Path("data/atlas_learning/experiences.sqlite3")


@dataclass(frozen=True, slots=True)
class _Work:
    work_id: str
    work_type: str
    payload_json: str
    accepted_at: datetime


class TradeIntelligenceService:
    """Research-only sidecar with no execution dependencies or authority.

    Producer calls perform validation, immutable bounded serialization, and
    ``put_nowait`` only. SQLite/outcomes/history remain on the worker thread.
    """

    def __init__(
        self, path: str | Path = DEFAULT_STORE_PATH, *, capacity: int = 4096,
        clock: Callable[[], datetime] | None = None,
        store_factory=ExperienceStore,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._path = Path(path)
        self._capacity = capacity
        self._clock = clock or (lambda: datetime.now(UTC))
        self._store_factory = store_factory
        self._queue: Queue[_Work] = Queue(maxsize=capacity)
        self._stop = Event()
        self._lock = RLock()
        self._accepting = True
        self._accepted = self._checkpointed = self._started = self._completed = 0
        self._suppressed = self._rejected = self._failed = 0
        self._hwm = self._pressure_episodes = self._pressure_recoveries = 0
        self._pressure_active = False
        self._recent: OrderedDict[str, None] = OrderedDict()
        self._active_count = 0
        self._lag_max_ms = 0
        self._lag_samples: deque[int] = deque(maxlen=2048)
        self._experiences_created = self._decisions_recorded = 0
        self._outcomes_completed = self._profitable_misses = 0
        self._protected_rejections = 0
        self._discovery_worker = DiscoveryWorker(state_limit=max(1000, capacity * 2))
        self._discovery_snapshot = self._discovery_worker.telemetry()
        self._discovery_contexts = {}
        self._thread = Thread(target=self._run, name="atlas-trade-intelligence", daemon=True)
        self._thread.start()

    def submit_experience(self, value: TradeOpportunityExperience) -> bool:
        if not isinstance(value, TradeOpportunityExperience):
            return self._reject()
        payload = experience_payload(value)
        return self._submit(_Work(value.experience_id, "EXPERIENCE", payload, self._now()))

    def observe_completed_bar(self, value: PriceBar) -> bool:
        if not isinstance(value, PriceBar):
            return self._reject()
        payload = canonical_json(asdict(value))
        identity = sha256(f"bar|{value.symbol.upper()}|{value.timestamp.isoformat()}|{payload}".encode()).hexdigest()
        return self._submit(_Work(identity, "BAR", payload, self._now()))

    def submit_decision(self, value: DecisionObservation) -> bool:
        if not isinstance(value, DecisionObservation):
            return self._reject()
        return self._submit(_Work(
            value.decision_id, "DECISION", canonical_json(asdict(value)), self._now()
        ))

    def observe_paper_execution(self, value: PaperExecutionObservation) -> bool:
        if not isinstance(value, PaperExecutionObservation):
            return self._reject()
        return self._submit(_Work(
            value.observation_id, "PAPER_OBSERVATION",
            canonical_json(asdict(value)), self._now(),
        ))

    def submit_discovery_observation(self, value: RuntimeDiscoveryObservation) -> bool:
        if not isinstance(value, RuntimeDiscoveryObservation):
            return self._reject()
        payload = canonical_json(discovery_observation_payload(value))
        identity = sha256(
            f"discovery|{value.context.symbol.upper()}|{value.context.decision_cutoff.isoformat()}".encode()
        ).hexdigest()
        return self._submit(_Work(identity, "DISCOVERY", payload, self._now()))

    def _submit(self, work: _Work) -> bool:
        with self._lock:
            if not self._accepting:
                return self._reject_locked()
            if len(work.payload_json.encode("utf-8")) > MAX_SERIALIZED_WORK_BYTES:
                return self._reject_locked()
            if work.work_id in self._recent:
                self._suppressed += 1
                return True
            try:
                self._queue.put_nowait(work)
            except Full:
                self._rejected += 1
                if not self._pressure_active:
                    self._pressure_active = True
                    self._pressure_episodes += 1
                return False
            self._accepted += 1
            self._remember(work.work_id)
            if self._pressure_active:
                self._pressure_active = False
                self._pressure_recoveries += 1
            self._hwm = max(self._hwm, self._queue.qsize())
            return True

    def close(self, *, timeout_seconds: float = 30) -> bool:
        if timeout_seconds < 0:
            raise ValueError("timeout must be nonnegative")
        with self._lock:
            self._accepting = False
            self._stop.set()
        self._thread.join(timeout_seconds)
        return not self._thread.is_alive()

    def metrics(self) -> WorkerMetrics:
        with self._lock:
            outstanding = self._accepted - self._completed - self._failed
            oldest = 0
            with self._queue.mutex:
                if self._queue.queue:
                    oldest = max(0, int((self._now() - self._queue.queue[0].accepted_at).total_seconds() * 1000))
            lag_values = sorted(self._lag_samples)
            return WorkerMetrics(
                self._accepted, self._checkpointed, self._started, self._completed,
                self._suppressed, self._rejected, self._failed, outstanding,
                self._queue.qsize(), self._hwm, self._pressure_episodes,
                self._pressure_recoveries, self._active_count, self._accepting,
                oldest, _percentile(lag_values, 0.50),
                _percentile(lag_values, 0.90), _percentile(lag_values, 0.99),
                self._lag_max_ms, self._experiences_created,
                self._decisions_recorded, self._outcomes_completed,
                self._profitable_misses, self._protected_rejections,
                self._discovery_snapshot.discovery_cycles,
                self._discovery_snapshot.detector_evaluations,
                self._discovery_snapshot.raw_detector_firings,
                self._discovery_snapshot.unique_detector_episodes,
                self._discovery_snapshot.normalized_opportunities,
                self._discovery_snapshot.strategy_memberships,
                self._discovery_snapshot.strategy_transitions,
                self._discovery_snapshot.position_correlations,
                self._discovery_snapshot.thesis_observations,
                self._discovery_snapshot.add_on_candidates,
            )

    def discovery_telemetry(self) -> DiscoveryTelemetry:
        with self._lock:
            return self._discovery_snapshot

    def discovery_context(self, symbol: str, cutoff: datetime):
        """Return only worker-completed context known by the supplied cutoff."""

        with self._lock:
            context = self._discovery_contexts.get(symbol.strip().upper())
        if context is None or context.observed_at > cutoff:
            return None
        return context

    @property
    def thread(self) -> Thread:
        return self._thread

    def _run(self) -> None:
        store = None
        active: dict[str, TradeOpportunityExperience] = {}
        completed_horizons: dict[str, set[int]] = {}
        deferred: dict[str, list[_Work]] = {}
        try:
            store = self._store_factory(self._path)
            store.recover_started_work()
            active = {item.experience_id: item for item in store.incomplete_experiences()}
            completed_horizons = {
                identity: {item.horizon_minutes for item in store.outcomes(identity)}
                for identity in active
            }
            for identity, experience in tuple(active.items()):
                decisions = store.decision_observations(identity)
                if decisions:
                    latest = decisions[-1]
                    active[identity] = replace(
                        experience,
                        actually_traded=(
                            experience.actually_traded or latest.actually_traded
                        ),
                    )
                if store.has_actual_paper_execution(identity):
                    active[identity] = replace(active[identity], actually_traded=True)
            recovered = [
                _Work(work_id, work_type, payload, accepted_at)
                for work_id, work_type, payload, accepted_at in store.recoverable_work()
            ]
            with self._lock:
                self._accepted += len(recovered)
                self._checkpointed += len(recovered)
            for work in recovered:
                self._process(
                    store, active, completed_horizons, deferred, work,
                    already_checkpointed=True,
                )
            self._set_active(len(active))
            while not self._stop.is_set() or not self._queue.empty():
                try:
                    work = self._queue.get(timeout=0.05)
                except Empty:
                    continue
                try:
                    self._process(store, active, completed_horizons, deferred, work)
                finally:
                    self._queue.task_done()
            for waiting in tuple(deferred.values()):
                for work in waiting:
                    self._terminal_failure(
                        store, work,
                        "MissingPrerequisiteError",
                        "parent experience was never durably established before shutdown",
                        prerequisite_related=True,
                    )
            deferred.clear()
        except Exception:
            with self._lock:
                self._failed += 1
                self._accepting = False
        finally:
            if store is not None:
                with self._lock:
                    suppressed = self._suppressed
                    rejected = self._rejected
                    pressure = self._pressure_episodes
                try:
                    store.record_admission_accounting(
                        suppressed=suppressed, rejected=rejected,
                        pressure_episodes=pressure,
                    )
                except Exception:
                    with self._lock:
                        self._failed += 1

    def _process(
        self, store, active, completed_horizons, deferred, work,
        *, already_checkpointed=False,
    ):
        now = self._now()
        lag_ms = max(0, int((now - work.accepted_at).total_seconds() * 1000))
        with self._lock:
            self._lag_max_ms = max(self._lag_max_ms, lag_ms)
            self._lag_samples.append(lag_ms)
        try:
            if not already_checkpointed:
                if store.checkpoint_work(work.work_id, work.work_type, work.accepted_at, work.payload_json):
                    with self._lock:
                        self._checkpointed += 1
            parent_id = _experience_dependency(work)
            if parent_id is not None:
                if (
                    parent_id not in active
                    and store.get_experience(parent_id) is None
                ):
                    parent_state = store.work_state(parent_id)
                    waiting_count = sum(len(items) for items in deferred.values())
                    if parent_state == "FAILED":
                        self._terminal_failure(
                            store, work, "MissingPrerequisiteError",
                            "parent experience work failed",
                            prerequisite_related=True,
                        )
                    elif waiting_count >= self._capacity:
                        self._terminal_failure(
                            store, work, "DependencyCapacityError",
                            "bounded prerequisite deferral capacity exhausted",
                            prerequisite_related=True,
                        )
                    else:
                        store.defer_work(
                            work.work_id, now,
                            dependency_type="EXPERIENCE",
                            dependency_id=parent_id,
                        )
                        deferred.setdefault(parent_id, []).append(work)
                    return
            store.start_work(work.work_id, now)
            with self._lock:
                self._started += 1
            if work.work_type == "EXPERIENCE":
                exp = _experience_from_json(work.payload_json)
                inserted = store.put_experience(exp)
                if inserted:
                    with self._lock:
                        self._experiences_created += 1
                existing = {item.horizon_minutes for item in store.outcomes(exp.experience_id)}
                if len(existing) < len(HORIZONS_MINUTES):
                    active[exp.experience_id] = exp
                    completed_horizons[exp.experience_id] = existing
            elif work.work_type == "DECISION":
                decision = _decision_from_json(work.payload_json)
                if store.put_decision_observation(decision):
                    with self._lock:
                        self._decisions_recorded += 1
                experience = active.get(decision.experience_id)
                if experience is None:
                    experience = store.get_experience(decision.experience_id)
                    existing = {
                        item.horizon_minutes
                        for item in store.outcomes(decision.experience_id)
                    }
                    if len(existing) < len(HORIZONS_MINUTES):
                        completed_horizons[decision.experience_id] = existing
                    else:
                        experience = None
                if experience is not None:
                    active[decision.experience_id] = replace(
                        experience,
                        actually_traded=(
                            experience.actually_traded or decision.actually_traded
                        ),
                    )
            elif work.work_type == "PAPER_OBSERVATION":
                paper = _paper_observation_from_json(work.payload_json)
                store.put_paper_execution_observation(paper)
                if (
                    paper.experience_id in active
                    and paper.event_type in {"ORDER_FILLED", "ORDER_PARTIALLY_FILLED"}
                ):
                    active[paper.experience_id] = replace(
                        active[paper.experience_id], actually_traded=True,
                    )
            elif work.work_type == "BAR":
                bar = _bar_from_json(work.payload_json)
                relevant = [item for item in active.values() if item.key.symbol.upper() == bar.symbol.upper()]
                if relevant:
                    store.put_bar(bar)
                    bars = store.bars(bar.symbol)
                    engine = OutcomeEngine()
                    for exp in relevant:
                        existing = completed_horizons.setdefault(exp.experience_id, set())
                        for outcome in engine.evaluate(exp, bars):
                            if outcome.horizon_minutes not in existing and store.put_outcome(outcome):
                                existing.add(outcome.horizon_minutes)
                                with self._lock:
                                    self._outcomes_completed += 1
                        if len(existing) == len(HORIZONS_MINUTES):
                            final = next(
                                item for item in engine.evaluate(exp, bars)
                                if item.horizon_minutes == max(HORIZONS_MINUTES)
                            )
                            classification = classify_missed_opportunity(exp, final)
                            with self._lock:
                                if classification is MissedOpportunityClassification.PROFITABLE_MISSED_OPPORTUNITY:
                                    self._profitable_misses += 1
                                elif classification is MissedOpportunityClassification.PROTECTED_REJECTION:
                                    self._protected_rejections += 1
                            active.pop(exp.experience_id, None)
                            completed_horizons.pop(exp.experience_id, None)
                    store.prune_bars()
            elif work.work_type == "DISCOVERY":
                discovery = discovery_observation_from_dict(json.loads(work.payload_json))
                self._discovery_worker.process(store, discovery)
                context = self._discovery_worker.context_for(
                    discovery.context.symbol,
                )
                with self._lock:
                    self._discovery_snapshot = self._discovery_worker.telemetry()
                    if context is not None:
                        self._discovery_contexts[context.symbol] = context
            else:
                raise ValueError("unknown research work type")
            store.complete_work(work.work_id, self._now())
            with self._lock:
                self._completed += 1
                self._active_count = len(active)
            if work.work_type == "EXPERIENCE":
                for child in deferred.pop(work.work_id, []):
                    self._process(
                        store, active, completed_horizons, deferred, child,
                        already_checkpointed=True,
                    )
        except Exception as exc:
            self._terminal_failure(
                store, work, type(exc).__name__, str(exc),
                prerequisite_related=False,
            )
            if work.work_type == "EXPERIENCE":
                for child in deferred.pop(work.work_id, []):
                    self._terminal_failure(
                        store, child, "MissingPrerequisiteError",
                        "parent experience work failed",
                        prerequisite_related=True,
                    )

    def _terminal_failure(
        self, store, work: _Work, error_class: str, message: str, *,
        prerequisite_related: bool,
    ) -> None:
        context = _failure_context(work)
        context.update({
            "data_lost": True,
            "error_class": error_class,
            "message": message,
            "prerequisite_related": prerequisite_related,
            "retryable": False,
            "timestamp": self._now().isoformat(),
        })
        store.complete_work(work.work_id, self._now(), canonical_json(context))
        with self._lock:
            self._failed += 1

    def _remember(self, identity: str) -> None:
        self._recent[identity] = None
        self._recent.move_to_end(identity)
        while len(self._recent) > self._capacity * 2:
            self._recent.popitem(last=False)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("research clock must be timezone-aware")
        return value.astimezone(UTC)

    def _reject(self) -> bool:
        with self._lock:
            return self._reject_locked()

    def _reject_locked(self) -> bool:
        self._rejected += 1
        return False

    def _set_active(self, value: int) -> None:
        with self._lock:
            self._active_count = value


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    return values[min(len(values) - 1, int((len(values) - 1) * fraction))]


def _failure_context(work: _Work) -> dict[str, object]:
    try:
        payload = json.loads(work.payload_json)
    except (TypeError, ValueError):
        payload = {}
    key = payload.get("key") if isinstance(payload.get("key"), dict) else {}
    return {
        "experience_id": payload.get("experience_id") or (
            work.work_id if work.work_type == "EXPERIENCE" else None
        ),
        "symbol": payload.get("symbol") or key.get("symbol"),
        "work_type": work.work_type,
    }


def _experience_dependency(work: _Work) -> str | None:
    """Return only an explicitly correlated, durable experience dependency."""

    if work.work_type == "DECISION":
        return _decision_from_json(work.payload_json).experience_id
    if work.work_type == "PAPER_OBSERVATION":
        return _paper_observation_from_json(work.payload_json).experience_id
    return None
