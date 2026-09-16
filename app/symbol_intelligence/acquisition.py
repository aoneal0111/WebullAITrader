"""Bounded, offline-capable SEC Symbol Intelligence acquisition orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import IntEnum, StrEnum
from threading import Event, Lock, Thread, current_thread
import time

from app.configuration.models import SymbolIntelligenceSECEdgarConfiguration

from .models import SourceAvailability, SourceStateSnapshot
from .providers.sec_filings import SecFilingFactNormalizer
from .providers.sec_identity import (
    SecIssuerIdentity,
    SecIssuerIdentityResolver,
    parse_sec_ticker_map,
)
from .providers.sec_transport import (
    SecAcquisitionFailureKind,
    SecEdgarEndpointClass,
    SecEdgarRequest,
)


class AcquisitionPriority(IntEnum):
    WATCH = 1
    ELEVATED = 2
    HIGH_PRIORITY = 3
    ACTIVE_OPPORTUNITY = 4


class AcquisitionFailureKind(StrEnum):
    TRANSPORT = "TRANSPORT"
    TICKER_PARSE = "TICKER_PARSE"
    IDENTITY_APPLY = "IDENTITY_APPLY"
    RESOLVER_RECOVER = "RESOLVER_RECOVER"
    NORMALIZATION = "NORMALIZATION"
    REPOSITORY_WRITE = "REPOSITORY_WRITE"
    INVALID_IDENTITY = "INVALID_IDENTITY"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True, slots=True)
class SecAcquisitionMetrics:
    ticker_refresh_attempts: int = 0
    ticker_refresh_successes: int = 0
    ticker_refresh_failures: int = 0
    issuer_refresh_requests: int = 0
    issuer_refresh_coalesced: int = 0
    issuer_refresh_dropped: int = 0
    issuer_acquisition_attempts: int = 0
    issuer_acquisition_successes: int = 0
    issuer_acquisition_failures: int = 0
    facts_emitted: int = 0
    facts_inserted: int = 0
    facts_deduplicated: int = 0
    normalization_failures: int = 0
    unresolved_identity_requests: int = 0
    resolver_recover_failures: int = 0
    repository_write_failures: int = 0
    cycles: int = 0
    cycle_budget_exhaustions: int = 0
    coalesced_count: int = 0
    dropped_count: int = 0
    evicted_count: int = 0
    unexpected_worker_failures: int = 0
    shutdown_timeouts: int = 0
    last_source_success: datetime | None = None


@dataclass(frozen=True, slots=True)
class SecAcquisitionDiagnostics:
    enabled: bool
    running: bool
    queue_size: int
    queue_capacity: int
    resolver_identity_count: int
    ticker_refresh_due: bool
    last_ticker_success: datetime | None
    last_source_success: datetime | None
    recent_failure_counts: tuple[tuple[str, int], ...]
    shutdown_incomplete: bool = False
    worker_alive: bool = False


@dataclass(slots=True)
class _Target:
    identity: SecIssuerIdentity
    priority: AcquisitionPriority
    requested_at: datetime
    next_due: float
    last_attempt: datetime | None = None
    last_success: datetime | None = None


class SecSymbolIntelligenceAcquisitionService:
    """Coordinates bounded SEC acquisition; never runs on a market-data hot path."""

    QUEUE_CAPACITY = 256
    MAX_SUBMISSIONS_PER_CYCLE = 32
    DEFAULT_CYCLE_BUDGET_SECONDS = 30.0

    def __init__(
        self,
        configuration: SymbolIntelligenceSECEdgarConfiguration,
        repository: object,
        transport: object,
        resolver: SecIssuerIdentityResolver,
        *,
        normalizer: SecFilingFactNormalizer | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        cycle_budget_seconds: float = DEFAULT_CYCLE_BUDGET_SECONDS,
        queue_capacity: int = QUEUE_CAPACITY,
    ) -> None:
        if queue_capacity <= 0 or queue_capacity > self.QUEUE_CAPACITY:
            raise ValueError("queue_capacity must be in 1..256")
        if cycle_budget_seconds <= 0:
            raise ValueError("cycle budget must be positive")
        self.configuration = configuration
        self.repository = repository
        self.transport = transport
        self.resolver = resolver
        self.normalizer = normalizer or SecFilingFactNormalizer()
        self._clock, self._monotonic = clock, monotonic
        self._cycle_budget = float(cycle_budget_seconds)
        self._queue_capacity = queue_capacity
        self._targets: dict[int, _Target] = {}
        self._lock = Lock()
        self._metrics = SecAcquisitionMetrics()
        self._failure_counts: dict[str, int] = {}
        self._last_ticker_success: datetime | None = None
        self._next_ticker_due = 0.0
        self._closed = False
        self._shutdown_requested = False
        self._close_requested = False
        self._running = False
        self._stop = Event()
        self._wake = Event()
        self._worker: Thread | None = None

    @property
    def metrics(self) -> SecAcquisitionMetrics:
        with self._lock:
            return self._metrics

    @property
    def diagnostics(self) -> SecAcquisitionDiagnostics:
        with self._lock:
            return SecAcquisitionDiagnostics(
                bool(getattr(self.configuration, "enabled", False)), self._running,
                len(self._targets), self._queue_capacity, getattr(self.resolver, "size", 0),
                self._monotonic() >= self._next_ticker_due,
                self._last_ticker_success, self._metrics.last_source_success,
                tuple(sorted(self._failure_counts.items())),
                self._shutdown_requested and self._running,
                bool(self._worker and self._worker.is_alive()),
            )

    def recover(self) -> int:
        """Recover only the bounded current resolver cache."""
        return self.resolver.recover()

    def start(self) -> bool:
        with self._lock:
            if self._closed or self._shutdown_requested or self._close_requested:
                return False
            if self._running:
                return False
            self._running = True
            self._stop.clear()
            self._worker = Thread(target=self._run, name="sec-symbol-intelligence", daemon=False)
            self._worker.start()
            return True

    def stop(self, timeout_seconds: float = 5.0) -> bool:
        with self._lock:
            worker = self._worker
            if not self._running:
                return True
            # Admission closes before signaling the worker, so callers racing
            # with shutdown cannot add new work.
            self._shutdown_requested = True
            self._stop.set()
            self._wake.set()
        if worker is not None and worker is not current_thread():
            worker.join(max(0.0, timeout_seconds))
        with self._lock:
            done = not (worker and worker.is_alive())
            if done:
                self._running = False
                self._worker = None
                if not self._closed and not self._close_requested:
                    self._shutdown_requested = False
            else:
                self._inc_locked("shutdown_timeouts")
            return done

    def close_admission(self) -> None:
        """Reject future work without claiming an in-flight worker is stopped."""

        with self._lock:
            self._shutdown_requested = True

    def request_stop(self) -> None:
        """Close admission and signal the worker without waiting for it."""

        with self._lock:
            self._shutdown_requested = True
            self._stop.set()
            self._wake.set()

    def close(self, timeout_seconds: float = 5.0) -> bool:
        with self._lock:
            if self._closed:
                return True
            self._shutdown_requested = True
            self._close_requested = True
        stopped = self.stop(timeout_seconds)
        if not stopped:
            return False
        with self._lock:
            self._closed = True
        close = getattr(self.transport, "close", None)
        if callable(close):
            close()
        return stopped

    def enqueue_issuer(self, identity: SecIssuerIdentity, priority: AcquisitionPriority = AcquisitionPriority.WATCH) -> bool:
        now = self._clock().astimezone(UTC)
        if not isinstance(identity, SecIssuerIdentity):
            self._inc("unresolved_identity_requests")
            self._record_failure(AcquisitionFailureKind.INVALID_IDENTITY)
            return False
        with self._lock:
            if self._closed or self._shutdown_requested or self._close_requested or not getattr(self.configuration, "enabled", False):
                return False
            existing = self._targets.get(identity.cik)
            if existing is not None:
                if priority > existing.priority or (priority == existing.priority and now >= existing.requested_at):
                    existing.identity, existing.priority, existing.requested_at = identity, priority, now
                self._inc_locked("issuer_refresh_coalesced")
                self._inc_locked("coalesced_count")
                self._wake.set()
                return True
            if len(self._targets) >= self._queue_capacity:
                victim = min(self._targets.values(), key=lambda item: (item.priority, item.requested_at, item.identity.cik))
                if priority <= victim.priority:
                    self._inc_locked("issuer_refresh_dropped")
                    self._inc_locked("dropped_count")
                    return False
                self._targets.pop(victim.identity.cik)
                self._inc_locked("evicted_count")
            self._targets[identity.cik] = _Target(identity, priority, now, self._monotonic())
            self._inc_locked("issuer_refresh_requests")
            self._wake.set()
            return True

    request_issuer_refresh = enqueue_issuer

    enqueue = enqueue_issuer

    def run_once(self) -> None:
        started = self._monotonic()
        self._inc("cycles")
        if not getattr(self.configuration, "enabled", False):
            return
        if self._monotonic() >= self._next_ticker_due:
            self._refresh_ticker()
        processed = 0
        while processed < self.MAX_SUBMISSIONS_PER_CYCLE and self._monotonic() - started < self._cycle_budget:
            target = self._take_due_target()
            if target is None:
                break
            self._refresh_issuer(target)
            processed += 1
        if processed >= self.MAX_SUBMISSIONS_PER_CYCLE or self._monotonic() - started >= self._cycle_budget:
            self._inc("cycle_budget_exhaustions")

    refresh_once = run_once

    process_cycle = run_once

    def refresh_ticker_map(self) -> None:
        """Run one bounded ticker-map acquisition attempt when explicitly requested."""
        if getattr(self.configuration, "enabled", False):
            self._refresh_ticker()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                self._inc("unexpected_worker_failures")
                self._record_failure(AcquisitionFailureKind.INTERNAL_ERROR)
            self._wake.wait(0.25)
            self._wake.clear()

    def _refresh_ticker(self) -> None:
        self._inc("ticker_refresh_attempts")
        result = None
        for exchange in (True, False):
            try:
                result = self.transport.acquire(SecEdgarRequest.ticker_map(exchange))
                if getattr(result, "response", None) is not None:
                    response = result.response
                    ticker_map = parse_sec_ticker_map(response.content, source="SEC_EDGAR", observed_at=response.observed_at, max_entries=self.configuration.max_ticker_entries)
                    if not self.repository.apply_sec_ticker_map(ticker_map):
                        raise ValueError("ticker identity map was rejected")
                    try:
                        self.resolver.recover()
                    except Exception:
                        self._inc("resolver_recover_failures")
                        self._record_failure(AcquisitionFailureKind.RESOLVER_RECOVER)
                        return
                    now = self._clock().astimezone(UTC)
                    with self._lock:
                        self._last_ticker_success = now
                        self._next_ticker_due = self._monotonic() + self.configuration.ticker_refresh_seconds
                    self._inc("ticker_refresh_successes")
                    self._source_success(now)
                    return
            except Exception:
                self._record_failure(AcquisitionFailureKind.TICKER_PARSE if result is not None else AcquisitionFailureKind.TRANSPORT)
        self._inc("ticker_refresh_failures")
        with self._lock:
            self._next_ticker_due = self._monotonic() + self.configuration.ticker_refresh_seconds
        self._source_failure()

    def _take_due_target(self) -> _Target | None:
        now = self._monotonic()
        with self._lock:
            due = [item for item in self._targets.values() if item.next_due <= now]
            if not due:
                return None
            target = max(due, key=lambda item: (item.priority, -item.next_due, -item.identity.cik))
            target.last_attempt = self._clock().astimezone(UTC)
            return self._targets.pop(target.identity.cik)

    def _refresh_issuer(self, target: _Target) -> None:
        self._inc("issuer_acquisition_attempts")
        try:
            result = self.transport.acquire(SecEdgarRequest.submissions(target.identity.cik))
            response = getattr(result, "response", None)
            if response is None:
                self._inc("issuer_acquisition_failures")
                self._record_failure(AcquisitionFailureKind.TRANSPORT)
                return
            normalized = self.normalizer.normalize(target.identity, response.content, response.observed_at)
            if normalized.failure is not None:
                self._inc("normalization_failures")
                self._record_failure(AcquisitionFailureKind.NORMALIZATION)
                return
            self._inc_by("facts_emitted", len(normalized.events))
            for offset in range(0, len(normalized.events), 1000):
                batch = normalized.events[offset:offset + 1000]
                if not batch:
                    continue
                ingest = self.repository.append_evidence(batch)
                self._inc_by("facts_inserted", ingest.inserted)
                self._inc_by("facts_deduplicated", ingest.deduplicated)
            self._inc("issuer_acquisition_successes")
            now = self._clock().astimezone(UTC)
            self._source_success(now)
            target.last_success = now
        except Exception:
            self._inc("issuer_acquisition_failures")
            self._inc("repository_write_failures")
            self._record_failure(AcquisitionFailureKind.REPOSITORY_WRITE)
        finally:
            # Keep retry intent bounded and cadence-controlled after every result.
            target.next_due = self._monotonic() + float(self.configuration.submissions_refresh_seconds)
            with self._lock:
                if not self._closed and len(self._targets) < self._queue_capacity:
                    self._targets[target.identity.cik] = target

    def _source_success(self, observed: datetime) -> None:
        with self._lock:
            self._set_metrics_locked(last_source_success=observed)
        self._store_state(SourceAvailability.AVAILABLE, observed)

    def _source_failure(self) -> None:
        self._store_state(SourceAvailability.UNAVAILABLE, self._clock().astimezone(UTC))

    def _store_state(self, availability: SourceAvailability, observed: datetime) -> None:
        try:
            stale_after = observed + timedelta(days=max(1, int(getattr(self.configuration, "freshness_days", 1))))
            self.repository.store_source_state(SourceStateSnapshot("SEC_EDGAR", availability, observed, stale_after))
        except Exception:
            self._inc("repository_write_failures")

    def _record_failure(self, kind: AcquisitionFailureKind) -> None:
        with self._lock:
            key = kind.value
            self._failure_counts[key] = self._failure_counts.get(key, 0) + 1

    def _inc(self, field: str, amount: int = 1) -> None:
        with self._lock:
            self._inc_locked(field, amount)

    def _inc_by(self, field: str, amount: int) -> None:
        self._inc(field, amount)

    def _inc_locked(self, field: str, amount: int = 1) -> None:
        self._set_metrics_locked(**{field: getattr(self._metrics, field) + amount})

    def _set_metrics_locked(self, **changes: object) -> None:
        values = {name: getattr(self._metrics, name) for name in self._metrics.__dataclass_fields__}
        values.update(changes)
        self._metrics = SecAcquisitionMetrics(**values)


__all__ = [
    "AcquisitionFailureKind", "AcquisitionPriority", "SecAcquisitionDiagnostics",
    "SecAcquisitionMetrics", "SecSymbolIntelligenceAcquisitionService",
]
