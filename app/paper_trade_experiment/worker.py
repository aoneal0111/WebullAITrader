"""Bounded, non-authoritative worker for paper experiment research updates."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread
from time import monotonic
from typing import Callable

from app.momentum_scanner import ScannerDecision
from app.performance_diagnostics import performance_diagnostics

from .journal import (
    DEFAULT_MODEL_VERSION,
    DEFAULT_STRATEGY_VERSION,
    PaperTradeExperimentJournal,
)


_LOGGER = logging.getLogger("atlas.research")

# The August 31 incident produced an inferred peak of 6,366 queued decisions.
# 8,192 provides 28.7% headroom while keeping memory strictly bounded.
DEFAULT_RESEARCH_QUEUE_CAPACITY = 8192
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class ResearchDecisionWork:
    """Complete immutable input needed to reproduce the established journal call."""

    decision: ScannerDecision
    execution_environment: str
    strategy_version: str
    model_version: str
    enqueued_at: datetime


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


JournalFactory = Callable[[str | Path], PaperTradeExperimentJournal]


class PaperTradeExperimentWorker:
    """Own the research journal and execute its mutations off the market thread.

    The worker has no broker, PAPER gateway, order, or account dependency. Queue
    saturation permanently degrades this capture instance so an incomplete
    dataset is explicit rather than silently sampled.
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
        # ScannerDecision is a recursively immutable frozen dataclass. replace()
        # creates a distinct snapshot so no runtime-owned object is queued.
        work = ResearchDecisionWork(
            decision=replace(decision),
            execution_environment=self._environment,
            strategy_version=DEFAULT_STRATEGY_VERSION,
            model_version=DEFAULT_MODEL_VERSION,
            enqueued_at=now,
        )
        with self._lock:
            if not self._accepting or self._failed:
                self._rejected += 1
                performance_diagnostics.increment("research_events_rejected")
                return False
            try:
                self._queue.put_nowait(work)
            except Full:
                self._accepting = False
                self._failed = True
                self._rejected += 1
                self._failures += 1
                performance_diagnostics.increment("research_events_rejected")
                performance_diagnostics.increment("research_failures")
                self._log_failure("bounded research queue saturated")
                return False
            self._enqueued += 1
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
            # The in-flight SQLite call cannot be interrupted safely. Explicitly
            # reject all not-yet-started work so shutdown never leaves an
            # ambiguous partially queued dataset.
            self._discard_pending()
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
            )

    @property
    def thread(self) -> Thread:
        """Expose identity for deterministic lifecycle tests only."""

        return self._thread

    def _run(self) -> None:
        journal: PaperTradeExperimentJournal | None = None
        while not (self._stop_requested.is_set() and self._queue.empty()):
            try:
                work = self._queue.get(timeout=0.05)
            except Empty:
                continue
            try:
                if journal is None:
                    journal = self._journal_factory(self._path)
                started = self._aware_now()
                lag_ms = max(
                    0.0,
                    (started - work.enqueued_at).total_seconds() * 1000.0,
                )
                journal.record_scanner_decision(
                    work.decision,
                    execution_environment=work.execution_environment,
                    strategy_version=work.strategy_version,
                    model_version=work.model_version,
                )
                with self._lock:
                    self._completed += 1
                    self._maximum_lag_ms = max(self._maximum_lag_ms, lag_ms)
                performance_diagnostics.increment("research_events_completed")
                performance_diagnostics.record_research_worker_lag(lag_ms)
            except Exception as error:
                self._mark_failed("retrospective research update failed", error)
                self._discard_pending()
                return
            finally:
                self._queue.task_done()
                performance_diagnostics.set_research_queue_depth(self._queue.qsize())
        with self._lock:
            self._stopped = True

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

    def _reject(self, message: str) -> None:
        with self._lock:
            self._rejected += 1
        performance_diagnostics.increment("research_events_rejected")
        self._log_failure(message)

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
