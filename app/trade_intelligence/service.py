"""Bounded nonblocking producer boundary and isolated research worker."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread
from typing import Callable

from .experience_store import (
    ExperienceStore, _bar_from_json, _experience_from_json,
)
from .models import (
    HORIZONS_MINUTES, PriceBar, TradeOpportunityExperience, WorkerMetrics,
    canonical_json, experience_payload,
)
from .outcome_engine import OutcomeEngine

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
            return WorkerMetrics(
                self._accepted, self._checkpointed, self._started, self._completed,
                self._suppressed, self._rejected, self._failed, outstanding,
                self._queue.qsize(), self._hwm, self._pressure_episodes,
                self._pressure_recoveries, self._active_count, self._accepting,
            )

    @property
    def thread(self) -> Thread:
        return self._thread

    def _run(self) -> None:
        store = None
        active: dict[str, TradeOpportunityExperience] = {}
        try:
            store = self._store_factory(self._path)
            store.recover_started_work()
            active = {item.experience_id: item for item in store.incomplete_experiences()}
            recovered = [
                _Work(work_id, work_type, payload, accepted_at)
                for work_id, work_type, payload, accepted_at in store.recoverable_work()
            ]
            with self._lock:
                self._accepted += len(recovered)
                self._checkpointed += len(recovered)
            for work in recovered:
                self._process(store, active, work, already_checkpointed=True)
            self._set_active(len(active))
            while not self._stop.is_set() or not self._queue.empty():
                try:
                    work = self._queue.get(timeout=0.05)
                except Empty:
                    continue
                try:
                    self._process(store, active, work)
                finally:
                    self._queue.task_done()
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

    def _process(self, store, active, work, *, already_checkpointed=False):
        now = self._now()
        try:
            if not already_checkpointed:
                if store.checkpoint_work(work.work_id, work.work_type, work.accepted_at, work.payload_json):
                    with self._lock:
                        self._checkpointed += 1
            store.start_work(work.work_id, now)
            with self._lock:
                self._started += 1
            if work.work_type == "EXPERIENCE":
                exp = _experience_from_json(work.payload_json)
                store.put_experience(exp)
                if len(store.outcomes(exp.experience_id)) < len(HORIZONS_MINUTES):
                    active[exp.experience_id] = exp
            elif work.work_type == "BAR":
                bar = _bar_from_json(work.payload_json)
                relevant = [item for item in active.values() if item.key.symbol.upper() == bar.symbol.upper()]
                if relevant:
                    store.put_bar(bar)
                    bars = store.bars(bar.symbol)
                    engine = OutcomeEngine()
                    for exp in relevant:
                        existing = {item.horizon_minutes for item in store.outcomes(exp.experience_id)}
                        for outcome in engine.evaluate(exp, bars):
                            if outcome.horizon_minutes not in existing:
                                store.put_outcome(outcome)
                        if len(store.outcomes(exp.experience_id)) == len(HORIZONS_MINUTES):
                            active.pop(exp.experience_id, None)
                    store.prune_bars()
            else:
                raise ValueError("unknown research work type")
            store.complete_work(work.work_id, self._now())
            with self._lock:
                self._completed += 1
                self._active_count = len(active)
        except Exception as exc:
            store.complete_work(work.work_id, self._now(), f"{type(exc).__name__}: {exc}")
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
