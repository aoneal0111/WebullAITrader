"""Bounded, non-authoritative worker for paper experiment research updates."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from collections import deque
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread
from time import monotonic
from typing import Callable
from uuid import uuid4

from app.momentum_scanner import ScannerDecision
from app.performance_diagnostics import performance_diagnostics

from .journal import (
    DEFAULT_MODEL_VERSION,
    DEFAULT_STRATEGY_VERSION,
    PaperTradeExperimentJournal,
    PreparedResearchWork,
    logical_candidate_identity,
    logical_decision_state,
    logical_decision_state_signature,
    prepare_price_observation,
    prepare_research_work,
)


_LOGGER = logging.getLogger("atlas.research")

# The August 31 incident produced an inferred peak of 6,366 queued decisions.
# 8,192 provides 28.7% headroom while keeping memory strictly bounded.
DEFAULT_RESEARCH_QUEUE_CAPACITY = 8192
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 30.0
MAX_GAP_SYMBOLS = 32
SESSION_CHECKPOINT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class ResearchDecisionWork:
    """Complete immutable input needed to reproduce the established journal call."""

    decision: ScannerDecision | None
    execution_environment: str
    strategy_version: str
    model_version: str
    enqueued_at: datetime
    prepared: PreparedResearchWork


@dataclass(frozen=True, slots=True)
class ResearchWorkerMetrics:
    queue_depth: int
    queue_high_water: int
    enqueued: int
    completed: int
    rejected: int
    failures: int
    maximum_lag_ms: float
    accepting: bool
    failed: bool
    stopped: bool
    checkpointed: int = 0
    resumed: int = 0
    durable_outstanding: int = 0
    oldest_outstanding_age_ms: float = 0.0
    candidate_creations: int = 0
    observations_accepted: int = 0
    suppressed_duplicates: int = 0
    coalesced: int = 0
    pressure_episodes: int = 0
    legacy_outstanding: int = 0
    journal_path: str = ""
    decisions_received: int = 0
    complete_decisions_received: int = 0
    incomplete_decisions_received: int = 0
    evidence_incomplete_counts: tuple[tuple[str, int], ...] = ()
    last_decision_received_at: datetime | None = None
    last_work_completed_at: datetime | None = None
    capture_gap_episodes: int = 0
    capture_gap_observations: int = 0
    coalesced_observation_gaps: int = 0


JournalFactory = Callable[[str | Path], PaperTradeExperimentJournal]


class PaperTradeExperimentWorker:
    """Own the research journal and execute its mutations off the market thread.

    The worker has no broker, PAPER gateway, order, or account dependency. Queue
    queue pressure is explicit and recoverable: a full queue rejects that
    item, but later submissions may succeed after the worker drains capacity.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        execution_environment: str,
        capacity: int = DEFAULT_RESEARCH_QUEUE_CAPACITY,
        journal_factory: JournalFactory = PaperTradeExperimentJournal,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if capacity <= 0:
            raise ValueError("research queue capacity must be positive")
        environment = execution_environment.strip().upper()
        if environment not in {"PAPER", "TEST"}:
            raise ValueError("research worker requires PAPER or TEST")
        self._path = Path(path)
        self._environment = environment
        self._journal_factory = journal_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._queue: Queue[ResearchDecisionWork] = Queue(maxsize=capacity)
        self._stop_requested = Event()
        self._lock = RLock()
        self._accepting = True
        self._failed = False
        self._stopped = False
        self._queue_high_water = 0
        self._enqueued = 0
        self._completed = 0
        self._rejected = 0
        self._failures = 0
        self._maximum_lag_ms = 0.0
        self._failure_logged = False
        self._checkpointed = 0
        self._resumed = 0
        self._durable_outstanding = 0
        self._oldest_outstanding_at: datetime | None = None
        self._candidate_creations = 0
        self._observations_accepted = 0
        self._suppressed_duplicates = 0
        self._coalesced = 0
        self._pressure_episodes = 0
        self._pressure_active = False
        self._legacy_outstanding = 0
        self._decisions_received = 0
        self._complete_decisions_received = 0
        self._incomplete_decisions_received = 0
        self._evidence_incomplete_counts = {
            "CATALYST_EVIDENCE_UNAVAILABLE": 0,
            "QUOTE_EVIDENCE_NOT_RECORDED": 0,
            "SPREAD_EVIDENCE_NOT_RECORDED": 0,
            "FLOAT_EVIDENCE_NOT_RECORDED": 0,
            "HALT_STATE_NOT_RECORDED": 0,
            "CATALYST_SOURCE_NOT_RECORDED": 0,
            "CATALYST_PUBLISHED_AT_NOT_RECORDED": 0,
            "DECISION_TIMESTAMP_NOT_RECORDED": 0,
            "LAST_PRICE_NOT_RECORDED": 0,
        }
        self._last_decision_received_at: datetime | None = None
        self._last_work_completed_at: datetime | None = None
        self._session_id = "research-session-" + uuid4().hex
        self._session_started_at = self._aware_now()
        self._last_session_checkpoint = monotonic()
        self._active_gap: dict[str, object] | None = None
        self._active_coalesced_gap: dict[str, object] | None = None
        self._pending_gaps: deque[dict[str, object]] = deque()
        self._capture_gap_episodes = 0
        self._capture_gap_observations = 0
        self._coalesced_observation_gaps = 0
        self._last_accepted_observation: dict[
            str, tuple[datetime, object]
        ] = {}
        self._decision_state_signatures: dict[str, str] = {}
        self._decision_states: dict[str, object] = {}
        self._pending_ids: set[str] = set()
        # Price observations are retrospective learning inputs, not execution
        # authority. Keep at most one queued/in-flight observation plus the
        # newest replacement per symbol so a slow journal cannot accumulate
        # minutes of obsolete ticks.
        self._pending_price_symbols_by_id: dict[str, str] = {}
        self._coalesced_price_work: dict[str, ResearchDecisionWork] = {}
        self._coalesced_price_timestamp: dict[str, datetime] = {}
        self._thread = Thread(
            target=self._run,
            name="atlas-experiment-research",
            daemon=True,
        )
        self._thread.start()

    def __call__(self, decision: ScannerDecision) -> bool:
        return self.submit(decision)

    def submit(self, decision: ScannerDecision) -> bool:
        if not isinstance(decision, ScannerDecision):
            self._reject("invalid immutable scanner decision")
            return False
        now = self._aware_now()
        with self._lock:
            self._decisions_received += 1
            self._last_decision_received_at = now
            if decision.timestamp is None:
                self._evidence_incomplete_counts[
                    "DECISION_TIMESTAMP_NOT_RECORDED"
                ] += 1
            if decision.price is None:
                self._evidence_incomplete_counts[
                    "LAST_PRICE_NOT_RECORDED"
                ] += 1
            if decision.timestamp is None or decision.price is None:
                self._incomplete_decisions_received += 1
            else:
                self._complete_decisions_received += 1
                self._record_evidence_completeness(decision)
        if decision.timestamp is None or decision.price is None:
            self._reject("incomplete scanner decision")
            return False
        snapshot = replace(decision)
        assert snapshot.timestamp is not None and snapshot.price is not None
        symbol = snapshot.symbol.strip().upper()
        observation = (
            (snapshot.last_price_timestamp or snapshot.timestamp).astimezone(UTC),
            snapshot.price,
        )
        decision_state = logical_decision_state(snapshot)
        # ScannerDecision is a recursively immutable frozen dataclass. replace()
        # creates a distinct snapshot so no runtime-owned object is queued.
        with self._lock:
            if not self._accepting or self._failed:
                self._rejected += 1
                performance_diagnostics.increment("research_events_rejected")
                return False
            observation_changed = (
                self._last_accepted_observation.get(symbol) != observation
            )
            state_changed = self._decision_states.get(symbol) != decision_state
            if not observation_changed and not state_changed:
                self._suppressed_duplicates += 1
                return True
            state_signature = (
                logical_decision_state_signature(snapshot)
                if state_changed else self._decision_state_signatures[symbol]
            )
            if self._queue.full():
                self._record_pressure_rejection(
                    symbol=symbol,
                    source_timestamp=(
                        snapshot.last_price_timestamp or snapshot.timestamp
                    ),
                )
                return False
            identity = (
                logical_candidate_identity(
                    snapshot,
                    state_signature=state_signature,
                    execution_environment=self._environment,
                    strategy_version=DEFAULT_STRATEGY_VERSION,
                    model_version=DEFAULT_MODEL_VERSION,
                )
                if state_changed else None
            )
            work = ResearchDecisionWork(
                decision=snapshot,
                execution_environment=self._environment,
                strategy_version=DEFAULT_STRATEGY_VERSION,
                model_version=DEFAULT_MODEL_VERSION,
                enqueued_at=now,
                prepared=prepare_research_work(
                    snapshot,
                    execution_environment=self._environment,
                    strategy_version=DEFAULT_STRATEGY_VERSION,
                    model_version=DEFAULT_MODEL_VERSION,
                    enqueued_at=now,
                    create_candidate=state_changed,
                    logical_identity=identity,
                ),
            )
            if work.prepared.work_id in self._pending_ids:
                self._suppressed_duplicates += 1
                return True
            try:
                self._queue.put_nowait(work)
            except Full:
                self._record_pressure_rejection()
                return False
            self._enqueued += 1
            self._pending_ids.add(work.prepared.work_id)
            if observation_changed:
                self._last_accepted_observation[symbol] = observation
                self._observations_accepted += 1
            if state_changed:
                self._decision_states[symbol] = decision_state
                self._decision_state_signatures[symbol] = state_signature
                self._candidate_creations += 1
            self._record_pressure_recovery()
            depth = self._queue.qsize()
            self._queue_high_water = max(self._queue_high_water, depth)
            performance_diagnostics.increment("research_events_enqueued")
            performance_diagnostics.set_research_queue_depth(depth)
            return True

    def observe_price(
        self, symbol: str, timestamp: datetime, price: Decimal,
    ) -> bool:
        """Nonblocking market-observation admission without candidate creation."""

        try:
            normalized = symbol.strip().upper()
            observed_at = timestamp.astimezone(UTC)
            observed = Decimal(price)
            now = self._aware_now()
        except (AttributeError, TypeError, ValueError):
            self._reject("invalid research price observation")
            return False
        observation = (observed_at, observed)
        with self._lock:
            if not self._accepting or self._failed:
                self._rejected += 1
                performance_diagnostics.increment("research_events_rejected")
                return False
            if self._last_accepted_observation.get(normalized) == observation:
                self._suppressed_duplicates += 1
                return True
            pending_symbol = normalized in self._pending_price_symbols_by_id.values()
            if not pending_symbol and self._queue.full():
                self._record_pressure_rejection(
                    symbol=normalized, source_timestamp=observed_at,
                )
                return False
            prepared = prepare_price_observation(
                normalized, observed_at, observed, enqueued_at=now
            )
            if prepared.work_id in self._pending_ids:
                self._suppressed_duplicates += 1
                return True
            work = ResearchDecisionWork(
                decision=None,
                execution_environment=self._environment,
                strategy_version=DEFAULT_STRATEGY_VERSION,
                model_version=DEFAULT_MODEL_VERSION,
                enqueued_at=now,
                prepared=prepared,
            )
            if pending_symbol:
                displaced = self._coalesced_price_work.get(normalized)
                displaced_at = self._coalesced_price_timestamp.get(normalized)
                self._coalesced_price_work[normalized] = work
                self._coalesced_price_timestamp[normalized] = observed_at
                self._coalesced += 1
                if displaced is not None:
                    assert displaced_at is not None
                    self._coalesced_observation_gaps += 1
                    self._record_coalesced_observation(
                        symbol=normalized,
                        source_timestamp=displaced_at,
                    )
                self._observations_accepted += 1
                self._last_accepted_observation[normalized] = observation
                performance_diagnostics.increment("research_events_coalesced")
                return True
            try:
                self._queue.put_nowait(work)
            except Full:
                self._record_pressure_rejection()
                return False
            self._enqueued += 1
            self._observations_accepted += 1
            self._last_accepted_observation[normalized] = observation
            self._pending_ids.add(prepared.work_id)
            self._pending_price_symbols_by_id[prepared.work_id] = normalized
            self._record_pressure_recovery()
            depth = self._queue.qsize()
            self._queue_high_water = max(self._queue_high_water, depth)
            performance_diagnostics.increment("research_events_enqueued")
            performance_diagnostics.set_research_queue_depth(depth)
            return True

    def close(
        self,
        *,
        timeout_seconds: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    ) -> bool:
        """Stop accepting work and drain FIFO work within a bounded wait."""

        if timeout_seconds < 0:
            raise ValueError("research shutdown timeout cannot be negative")
        with self._lock:
            if self._stopped:
                return not self._thread.is_alive()
            self._accepting = False
            self._stop_requested.set()
        self._thread.join(timeout_seconds)
        stopped = not self._thread.is_alive()
        with self._lock:
            self._stopped = stopped
            if not stopped:
                self._failed = True
                self._failures += 1
                performance_diagnostics.increment("research_failures")
                self._log_failure("research worker did not drain before shutdown timeout")
        if not stopped:
            # In-flight SQLite work is already checkpointed. Persist the
            # producer queue without waiting indefinitely for that operation.
            self._checkpoint_pending_from_shutdown()
        return stopped

    def metrics(self) -> ResearchWorkerMetrics:
        with self._lock:
            return ResearchWorkerMetrics(
                queue_depth=self._queue.qsize(),
                queue_high_water=self._queue_high_water,
                enqueued=self._enqueued,
                completed=self._completed,
                rejected=self._rejected,
                failures=self._failures,
                maximum_lag_ms=self._maximum_lag_ms,
                accepting=self._accepting,
                failed=self._failed,
                stopped=self._stopped,
                checkpointed=self._checkpointed,
                resumed=self._resumed,
                durable_outstanding=self._durable_outstanding,
                oldest_outstanding_age_ms=(
                    0.0 if self._oldest_outstanding_at is None else max(
                        0.0,
                        (self._aware_now() - self._oldest_outstanding_at)
                        .total_seconds() * 1000.0,
                    )
                ),
                candidate_creations=self._candidate_creations,
                observations_accepted=self._observations_accepted,
                suppressed_duplicates=self._suppressed_duplicates,
                coalesced=self._coalesced,
                pressure_episodes=self._pressure_episodes,
                legacy_outstanding=self._legacy_outstanding,
                journal_path=str(self._path.resolve()),
                decisions_received=self._decisions_received,
                complete_decisions_received=self._complete_decisions_received,
                incomplete_decisions_received=self._incomplete_decisions_received,
                evidence_incomplete_counts=tuple(sorted(
                    self._evidence_incomplete_counts.items()
                )),
                last_decision_received_at=self._last_decision_received_at,
                last_work_completed_at=self._last_work_completed_at,
                capture_gap_episodes=self._capture_gap_episodes,
                capture_gap_observations=self._capture_gap_observations,
                coalesced_observation_gaps=self._coalesced_observation_gaps,
            )

    def reset_symbol(self, symbol: str) -> None:
        """End a logical decision episode at an authoritative stream reset."""

        normalized = symbol.strip().upper()
        with self._lock:
            self._last_accepted_observation.pop(normalized, None)
            self._decision_states.pop(normalized, None)
            self._decision_state_signatures.pop(normalized, None)

    @property
    def thread(self) -> Thread:
        """Expose identity for deterministic lifecycle tests only."""

        return self._thread

    def _run(self) -> None:
        journal: PaperTradeExperimentJournal | None = None
        durable: deque[PreparedResearchWork] = deque()
        supports_durable = False
        try:
            journal = self._journal_factory(self._path)
            self._persist_worker_session(journal)
            supports_durable = all(callable(getattr(journal, name, None)) for name in (
                "checkpoint_work_items", "recoverable_work_items",
                "process_prepared_work",
            ))
            if supports_durable:
                recovered = journal.recoverable_work_items()
                durable.extend(recovered)
                completeness = getattr(journal, "completeness_snapshot", None)
                legacy_outstanding = 0
                if callable(completeness):
                    legacy_outstanding = int(
                        completeness().get("legacy_outstanding_count", 0)
                    )
                with self._lock:
                    self._resumed += len(recovered)
                    self._legacy_outstanding = legacy_outstanding
                    self._durable_outstanding = len(durable)
                    self._oldest_outstanding_at = (
                        recovered[0].enqueued_at if recovered else None
                    )
            while not (
                self._stop_requested.is_set()
                and self._queue.empty()
                and not durable
            ):
                if supports_durable:
                    # Never drain the bounded queue into an unbounded secondary
                    # deque.  Checkpoint and service one bounded batch before
                    # admitting the next batch.
                    batch = (
                        [] if durable else
                        self._take_batch(block=True, limit=256)
                    )
                    if batch:
                        prepared = tuple(item.prepared for item in batch)
                        inserted, _duplicates = journal.checkpoint_work_items(prepared)
                        durable.extend(prepared)
                        with self._lock:
                            self._checkpointed += inserted
                            self._pending_ids.difference_update(
                                item.work_id for item in prepared
                            )
                            self._durable_outstanding = len(durable)
                            if self._oldest_outstanding_at is None:
                                self._oldest_outstanding_at = prepared[0].enqueued_at
                        for _item in batch:
                            self._queue.task_done()
                        performance_diagnostics.set_research_queue_depth(
                            self._queue.qsize()
                        )
                    if not durable:
                        continue
                    processing = tuple(
                        durable.popleft() for _ in range(min(256, len(durable)))
                    )
                    prepared_work = processing[0]
                    work = None
                else:
                    try:
                        work = self._queue.get(timeout=0.05)
                    except Empty:
                        continue
                    prepared_work = work.prepared
                if supports_durable:
                    started = self._aware_now()
                    process_batch = getattr(journal, "process_prepared_batch", None)
                    if callable(process_batch):
                        process_batch(processing)
                    else:
                        for item in processing:
                            journal.process_prepared_work(item)
                    lag_values = tuple(max(
                        0.0,
                        (started - item.enqueued_at).total_seconds() * 1000.0,
                    ) for item in processing)
                else:
                    assert work is not None
                    started = self._aware_now()
                    lag_values = (max(
                        0.0,
                        (started - prepared_work.enqueued_at).total_seconds() * 1000.0,
                    ),)
                    if work.decision is None:
                        payload = prepared_work.payload["observation"]
                        journal.observe_price(
                            str(payload["symbol"]),
                            datetime.fromisoformat(str(payload["timestamp"])),
                            Decimal(str(payload["price"])),
                        )
                    else:
                        journal.record_scanner_decision(
                            work.decision,
                            execution_environment=work.execution_environment,
                            strategy_version=work.strategy_version,
                            model_version=work.model_version,
                        )
                with self._lock:
                    self._completed += len(lag_values)
                    self._last_work_completed_at = self._aware_now()
                    if not supports_durable:
                        self._pending_ids.discard(prepared_work.work_id)
                    self._maximum_lag_ms = max(
                        self._maximum_lag_ms, *lag_values
                    )
                    if supports_durable:
                        self._durable_outstanding = len(durable)
                        self._oldest_outstanding_at = (
                            durable[0].enqueued_at if durable else None
                        )
                    for item in (
                        processing if supports_durable else (prepared_work,)
                    ):
                        self._complete_price_work(item.work_id)
                performance_diagnostics.increment(
                    "research_events_completed", len(lag_values)
                )
                performance_diagnostics.record_research_worker_lag(max(lag_values))
                self._persist_capture_gaps(journal)
                self._checkpoint_session_if_due(journal)
                if not supports_durable:
                    assert work is not None
                    self._queue.task_done()
                    performance_diagnostics.set_research_queue_depth(
                        self._queue.qsize()
                    )
        except Exception as error:
            self._mark_failed("retrospective research update failed", error)
            self._checkpoint_pending(journal)
            return
        finally:
            with self._lock:
                self._finish_active_gap()
                self._finish_coalesced_gap()
            self._persist_capture_gaps(journal)
            self._persist_worker_session(journal, ended_at=self._aware_now())
            telemetry = getattr(journal, "record_worker_telemetry", None)
            if callable(telemetry):
                try:
                    with self._lock:
                        telemetry(
                            rejected=self._rejected,
                            queue_high_water=self._queue_high_water,
                            lag_max_ms=self._maximum_lag_ms,
                            resumed=self._resumed,
                        )
                except Exception as error:
                    self._mark_failed("research telemetry checkpoint failed", error)
            close = getattr(journal, "close", None)
            if callable(close):
                close()
        with self._lock:
            self._stopped = True

    def _take_batch(
        self, *, block: bool, limit: int = 256,
    ) -> list[ResearchDecisionWork]:
        batch: list[ResearchDecisionWork] = []
        try:
            batch.append(
                self._queue.get(timeout=0.05) if block
                else self._queue.get_nowait()
            )
        except Empty:
            return batch
        while len(batch) < limit:
            try:
                batch.append(self._queue.get_nowait())
            except Empty:
                break
        return batch

    def _complete_price_work(self, work_id: str) -> None:
        """Release one price slot and admit only its newest replacement."""

        with self._lock:
            symbol = self._pending_price_symbols_by_id.pop(work_id, None)
            if symbol is None:
                return
            replacement = self._coalesced_price_work.pop(symbol, None)
            self._coalesced_price_timestamp.pop(symbol, None)
            if replacement is None:
                return
            try:
                self._queue.put_nowait(replacement)
            except Full:
                # The consumer owns this call, so a full queue means newer
                # authoritative decision work won admission first. Preserve
                # explicit pressure accounting instead of blocking it.
                self._record_pressure_rejection()
                return
            self._enqueued += 1
            self._pending_ids.add(replacement.prepared.work_id)
            self._pending_price_symbols_by_id[
                replacement.prepared.work_id
            ] = symbol
            depth = self._queue.qsize()
            self._queue_high_water = max(self._queue_high_water, depth)
            performance_diagnostics.increment("research_events_enqueued")
            performance_diagnostics.set_research_queue_depth(depth)

    def _checkpoint_pending(
        self, journal: PaperTradeExperimentJournal | None,
    ) -> None:
        pending = self._take_batch(block=False, limit=self._queue.maxsize)
        if not pending:
            return
        checkpoint = getattr(journal, "checkpoint_work_items", None)
        if callable(checkpoint):
            inserted, _duplicates = checkpoint(
                tuple(item.prepared for item in pending)
            )
            with self._lock:
                self._checkpointed += inserted
                self._durable_outstanding += inserted
                self._pending_ids.difference_update(
                    item.prepared.work_id for item in pending
                )
        else:
            with self._lock:
                self._rejected += len(pending)
            performance_diagnostics.increment(
                "research_events_rejected", len(pending)
            )
        for _item in pending:
            self._queue.task_done()
        performance_diagnostics.set_research_queue_depth(0)

    def _checkpoint_pending_from_shutdown(self) -> None:
        pending = self._take_batch(block=False, limit=self._queue.maxsize)
        if not pending:
            return
        journal = None
        try:
            journal = self._journal_factory(self._path)
            checkpoint = getattr(journal, "checkpoint_work_items", None)
            if not callable(checkpoint):
                raise RuntimeError("research journal does not support checkpointing")
            inserted, _duplicates = checkpoint(
                tuple(item.prepared for item in pending)
            )
            with self._lock:
                self._checkpointed += inserted
                self._durable_outstanding += inserted
                self._pending_ids.difference_update(
                    item.prepared.work_id for item in pending
                )
        except Exception as error:
            with self._lock:
                self._rejected += len(pending)
            performance_diagnostics.increment(
                "research_events_rejected", len(pending)
            )
            self._log_failure("research checkpoint failed", error)
        finally:
            close = getattr(journal, "close", None)
            if callable(close):
                close()
            for _item in pending:
                self._queue.task_done()
            performance_diagnostics.set_research_queue_depth(0)

    def _discard_pending(self) -> None:
        rejected = 0
        while True:
            try:
                self._queue.get_nowait()
            except Empty:
                break
            self._queue.task_done()
            rejected += 1
        if rejected:
            with self._lock:
                self._rejected += rejected
            performance_diagnostics.increment("research_events_rejected", rejected)
        performance_diagnostics.set_research_queue_depth(0)

    def _mark_failed(self, message: str, error: Exception) -> None:
        with self._lock:
            self._accepting = False
            self._failed = True
            self._failures += 1
        performance_diagnostics.increment("research_failures")
        self._log_failure(message, error)

    def _record_pressure_rejection(
        self, *, symbol: str | None = None,
        source_timestamp: datetime | None = None,
    ) -> None:
        now = self._aware_now()
        self._rejected += 1
        self._capture_gap_observations += 1
        if not self._pressure_active:
            self._pressure_active = True
            self._pressure_episodes += 1
            self._capture_gap_episodes += 1
            self._active_gap = {
                "gap_id": f"{self._session_id}:gap:{self._pressure_episodes}",
                "worker_session_id": self._session_id,
                "reason": "BOUNDED_RESEARCH_QUEUE_FULL",
                "started_at": now.isoformat(),
                "ended_at": now.isoformat(),
                "first_source_timestamp": None,
                "last_source_timestamp": None,
                "episode_count": 1,
                "rejected_count": 0,
                "affected_symbols": [],
                "affected_symbol_overflow": 0,
            }
            _LOGGER.error(
                "event_type=experiment_research_pressure "
                "reason=bounded_research_queue_full capacity=%d",
                self._queue.maxsize,
            )
        gap = self._active_gap
        assert gap is not None
        gap["ended_at"] = now.isoformat()
        gap["rejected_count"] = int(gap["rejected_count"]) + 1
        if source_timestamp is not None and source_timestamp.tzinfo is not None:
            source_text = source_timestamp.astimezone(UTC).isoformat()
            if gap["first_source_timestamp"] is None:
                gap["first_source_timestamp"] = source_text
            gap["last_source_timestamp"] = source_text
        if symbol:
            symbols = gap["affected_symbols"]
            assert isinstance(symbols, list)
            normalized = symbol.strip().upper()
            if normalized not in symbols:
                if len(symbols) < MAX_GAP_SYMBOLS:
                    symbols.append(normalized)
                else:
                    gap["affected_symbol_overflow"] = int(
                        gap["affected_symbol_overflow"]
                    ) + 1
        performance_diagnostics.increment("research_events_rejected")

    def _record_pressure_recovery(self) -> None:
        if self._pressure_active:
            self._pressure_active = False
            self._finish_active_gap()
            _LOGGER.info(
                "event_type=experiment_research_pressure_recovered "
                "queue_depth=%d",
                self._queue.qsize(),
            )

    def _finish_active_gap(self) -> None:
        if self._active_gap is not None:
            self._queue_gap(self._active_gap)
            self._active_gap = None

    def _record_coalesced_observation(
        self, *, symbol: str, source_timestamp: datetime,
    ) -> None:
        now = self._aware_now()
        self._capture_gap_observations += 1
        if self._active_coalesced_gap is None:
            self._capture_gap_episodes += 1
            self._active_coalesced_gap = {
                "gap_id": (
                    f"{self._session_id}:coalesced:"
                    f"{self._capture_gap_episodes}"
                ),
                "worker_session_id": self._session_id,
                "reason": "INTERMEDIATE_PRICE_OBSERVATION_COALESCED",
                "started_at": now.isoformat(),
                "ended_at": now.isoformat(),
                "first_source_timestamp": None,
                "last_source_timestamp": None,
                "episode_count": 1,
                "rejected_count": 0,
                "affected_symbols": [],
                "affected_symbol_overflow": 0,
            }
        gap = self._active_coalesced_gap
        gap["ended_at"] = now.isoformat()
        gap["rejected_count"] = int(gap["rejected_count"]) + 1
        source_text = source_timestamp.astimezone(UTC).isoformat()
        if gap["first_source_timestamp"] is None:
            gap["first_source_timestamp"] = source_text
        gap["last_source_timestamp"] = source_text
        symbols = gap["affected_symbols"]
        assert isinstance(symbols, list)
        if symbol not in symbols:
            if len(symbols) < MAX_GAP_SYMBOLS:
                symbols.append(symbol)
            else:
                gap["affected_symbol_overflow"] = int(
                    gap["affected_symbol_overflow"]
                ) + 1

    def _finish_coalesced_gap(self) -> None:
        if self._active_coalesced_gap is not None:
            self._queue_gap(self._active_coalesced_gap)
            self._active_coalesced_gap = None

    def _queue_gap(self, gap: dict[str, object]) -> None:
        """Keep one bounded pending aggregate per fixed gap reason."""

        existing = next((
            item for item in self._pending_gaps
            if item["reason"] == gap["reason"]
        ), None)
        if existing is None:
            self._pending_gaps.append(gap)
            return
        existing["ended_at"] = gap["ended_at"]
        existing["last_source_timestamp"] = gap["last_source_timestamp"]
        existing["episode_count"] = (
            int(existing.get("episode_count", 1))
            + int(gap.get("episode_count", 1))
        )
        existing["rejected_count"] = (
            int(existing["rejected_count"]) + int(gap["rejected_count"])
        )
        existing["affected_symbol_overflow"] = (
            int(existing["affected_symbol_overflow"])
            + int(gap["affected_symbol_overflow"])
        )
        existing_symbols = existing["affected_symbols"]
        incoming_symbols = gap["affected_symbols"]
        assert isinstance(existing_symbols, list)
        assert isinstance(incoming_symbols, list)
        for symbol in incoming_symbols:
            if symbol in existing_symbols:
                continue
            if len(existing_symbols) < MAX_GAP_SYMBOLS:
                existing_symbols.append(symbol)
            else:
                existing["affected_symbol_overflow"] = int(
                    existing["affected_symbol_overflow"]
                ) + 1

    def _persist_capture_gaps(self, journal: object | None) -> None:
        writer = getattr(journal, "record_capture_gaps", None)
        if not callable(writer):
            return
        with self._lock:
            self._finish_coalesced_gap()
            if not self._pending_gaps:
                return
            pending = tuple(self._pending_gaps)
            self._pending_gaps.clear()
        try:
            writer(pending)
        except Exception:
            with self._lock:
                self._pending_gaps.extendleft(reversed(pending))
            raise

    def _persist_worker_session(
        self, journal: object | None, *, ended_at: datetime | None = None,
    ) -> None:
        writer = getattr(journal, "record_worker_session", None)
        if not callable(writer):
            return
        with self._lock:
            snapshot = {
                "session_id": self._session_id,
                "started_at": self._session_started_at,
                "enqueued": self._enqueued,
                "completed": self._completed,
                "rejected": self._rejected,
                "queue_high_water": self._queue_high_water,
                "lag_max_ms": self._maximum_lag_ms,
                "pressure_episodes": self._pressure_episodes,
                "ended_at": ended_at,
            }
        writer(**snapshot)
        self._last_session_checkpoint = monotonic()

    def _checkpoint_session_if_due(self, journal: object | None) -> None:
        if monotonic() - self._last_session_checkpoint >= SESSION_CHECKPOINT_SECONDS:
            self._persist_worker_session(journal)

    def _reject(self, message: str) -> None:
        with self._lock:
            self._rejected += 1
        performance_diagnostics.increment("research_events_rejected")
        self._log_failure(message)

    def _record_evidence_completeness(self, decision: ScannerDecision) -> None:
        """Count fixed, non-sensitive reasons a recorded decision is incomplete."""

        if decision.catalyst_status.value in {"UNKNOWN", "UNAVAILABLE"}:
            self._evidence_incomplete_counts[
                "CATALYST_EVIDENCE_UNAVAILABLE"
            ] += 1
        if decision.bid is None or decision.ask is None:
            self._evidence_incomplete_counts[
                "QUOTE_EVIDENCE_NOT_RECORDED"
            ] += 1
        if decision.metrics.spread_percent is None:
            self._evidence_incomplete_counts[
                "SPREAD_EVIDENCE_NOT_RECORDED"
            ] += 1
        if decision.float_shares is None:
            self._evidence_incomplete_counts[
                "FLOAT_EVIDENCE_NOT_RECORDED"
            ] += 1
        if decision.halted is None or decision.tradable is None:
            self._evidence_incomplete_counts[
                "HALT_STATE_NOT_RECORDED"
            ] += 1
        if decision.catalyst_status.value == "TRUE":
            if decision.catalyst_source is None:
                self._evidence_incomplete_counts[
                    "CATALYST_SOURCE_NOT_RECORDED"
                ] += 1
            if decision.catalyst_published_at is None:
                self._evidence_incomplete_counts[
                    "CATALYST_PUBLISHED_AT_NOT_RECORDED"
                ] += 1

    def _log_failure(self, message: str, error: Exception | None = None) -> None:
        if self._failure_logged:
            return
        self._failure_logged = True
        _LOGGER.error(
            "event_type=experiment_research_degraded reason=%s error_type=%s",
            message,
            "none" if error is None else type(error).__name__,
            exc_info=(
                None
                if error is None
                else (type(error), error, error.__traceback__)
            ),
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("research worker clock must be timezone-aware")
        return value.astimezone(UTC)


__all__ = [
    "DEFAULT_RESEARCH_QUEUE_CAPACITY",
    "DEFAULT_SHUTDOWN_TIMEOUT_SECONDS",
    "PaperTradeExperimentWorker",
    "ResearchDecisionWork",
    "ResearchWorkerMetrics",
]
