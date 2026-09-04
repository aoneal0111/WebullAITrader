"""Bounded research runner and disabled-by-default scheduled sidecar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from pathlib import Path
from threading import Event, RLock, Thread
from time import monotonic, perf_counter
from typing import Callable, Mapping

from .evaluator import snapshot_from_rows
from .provider import (
    BroadDiscoveryRefresh,
    WebullBroadDiscoveryProvider,
    source_rows_by_symbol,
)
from .service import DynamicMomentumDiscoveryService
from .store import JsonLinesDiscoveryStore


_LOGGER = logging.getLogger("atlas.research.dynamic_momentum")


@dataclass(frozen=True, slots=True)
class CollectionResult:
    refresh: BroadDiscoveryRefresh | None
    assembled_symbols: int
    admitted_observations: int
    rejected_observations: int
    failure_type: str | None
    research_only: bool = True
    production_universe_mutated: bool = False
    execution_authorized: bool = False


class DynamicMomentumDiscoveryRunner:
    """Feeds only the dedicated research service and returns diagnostics."""

    def __init__(
        self, provider: WebullBroadDiscoveryProvider,
        service: DynamicMomentumDiscoveryService,
    ) -> None:
        self._provider = provider
        self._service = service

    def collect(
        self, *, breadth_per_source: int, observed_at: datetime, session: str,
        production_stages: Mapping[str, tuple[str, ...]] | None = None,
        production_stage_source: Callable[[str], tuple[str, ...]] | None = None,
    ) -> CollectionResult:
        try:
            refresh = self._provider.fetch(
                breadth_per_source=breadth_per_source,
                observed_at=observed_at,
                session=session,
            )
            grouped = source_rows_by_symbol(refresh)
            accepted = 0
            rejected = 0
            stages = production_stages or {}
            for symbol, rows in grouped.items():
                try:
                    snapshot = snapshot_from_rows(
                        rows, decision_cutoff=observed_at, session=session,
                        production_stages=(
                            production_stage_source(symbol)
                            if production_stage_source is not None
                            else stages.get(symbol, ())
                        ),
                    )
                except Exception:
                    rejected += 1
                    continue
                if self._service.observe(snapshot):
                    accepted += 1
                else:
                    rejected += 1
            return CollectionResult(
                refresh=refresh, assembled_symbols=len(grouped),
                admitted_observations=accepted,
                rejected_observations=rejected, failure_type=None,
            )
        except Exception as exc:
            return CollectionResult(
                refresh=None, assembled_symbols=0, admitted_observations=0,
                rejected_observations=0, failure_type=type(exc).__name__,
            )


@dataclass(frozen=True, slots=True)
class DynamicMomentumRuntimeMetrics:
    enabled: bool
    healthy: bool
    running: bool
    breadth: int
    refresh_seconds: int
    refresh_count: int
    provider_requests: int
    provider_failures: int
    rows_received: int
    unique_symbols: int
    production_overlap: int
    shadow_only_symbols: int
    episodes_accepted: int
    episodes_suppressed: int
    promotions: int
    queue_depth: int
    queue_high_water: int
    maximum_worker_lag_ms: float
    persistence_failures: int
    maximum_refresh_latency_ms: float
    maximum_producer_latency_ms: float
    retained_symbols: int
    memory_state_size: int
    persistence_path: str
    stopped: bool
    last_error_type: str | None
    event_counts: tuple[tuple[str, int], ...]


class DynamicMomentumDiscoveryRuntime:
    """Periodic broad screener research with no production return path."""

    def __init__(
        self,
        provider: WebullBroadDiscoveryProvider,
        *,
        enabled: bool,
        path: str | Path,
        comparison_source: Callable[[str], tuple[str, ...]],
        comparison_memory_source: Callable[[], int] = lambda: 0,
        comparison_retained_source: Callable[[], int] = lambda: 0,
        breadth: int = 100,
        refresh_seconds: int = 60,
        queue_capacity: int = 1024,
        maximum_retained_symbols: int = 1000,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        session_source: Callable[[datetime], str] = lambda _now: "UNKNOWN",
        store_factory: Callable[[Path], object] = JsonLinesDiscoveryStore,
        service_factory: Callable[..., DynamicMomentumDiscoveryService] = (
            DynamicMomentumDiscoveryService
        ),
        timer: Callable[[], float] = perf_counter,
    ) -> None:
        self.enabled = bool(enabled)
        self.path = Path(path)
        self._provider = provider
        self._comparison_source = comparison_source
        self._comparison_memory_source = comparison_memory_source
        self._comparison_retained_source = comparison_retained_source
        self._breadth = breadth
        self._refresh_seconds = refresh_seconds
        self._queue_capacity = queue_capacity
        self._maximum_retained_symbols = maximum_retained_symbols
        self._clock = clock
        self._session_source = session_source
        self._store_factory = store_factory
        self._service_factory = service_factory
        self._timer = timer
        self._lock = RLock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._service: DynamicMomentumDiscoveryService | None = None
        self._runner: DynamicMomentumDiscoveryRunner | None = None
        self._running = False
        self._stopped = not self.enabled
        self._refresh_count = 0
        self._provider_requests = self._provider_failures = 0
        self._rows_received = self._unique_symbols = 0
        self._production_overlap = self._shadow_only = 0
        self._max_refresh_ms = 0.0
        self._last_error_type: str | None = None
        self._configuration_error = (
            "BreadthMustBe100" if breadth != 100 else
            "RefreshCadenceTooFast" if refresh_seconds < 30 else
            "InvalidResearchBound"
            if queue_capacity <= 0 or maximum_retained_symbols <= 0 else None
        )

    def start(self) -> bool:
        if not self.enabled:
            return False
        with self._lock:
            if self._running:
                return True
            try:
                if self._configuration_error is not None:
                    raise ValueError(self._configuration_error)
                if self.path.suffix.lower() != ".jsonl":
                    raise ValueError("dynamic discovery path must be JSONL")
                if self.path.exists() and not self.path.is_file():
                    raise IsADirectoryError(str(self.path))
                store = self._store_factory(self.path)
                self._service = self._service_factory(
                    store,
                    enabled=True,
                    capacity=self._queue_capacity,
                    maximum_retained_symbols=self._maximum_retained_symbols,
                    clock=self._clock,
                    timer=self._timer,
                )
                self._runner = DynamicMomentumDiscoveryRunner(
                    self._provider, self._service
                )
                self._stop.clear()
                self._thread = Thread(
                    target=self._run,
                    name="atlas-dynamic-momentum-runtime",
                    daemon=True,
                )
                self._running = True
                self._stopped = False
                self._thread.start()
                return True
            except Exception as exc:
                self._record_failure(exc)
                self._stopped = True
                return False

    def refresh_once(self) -> CollectionResult | None:
        with self._lock:
            runner = self._runner
            if runner is None or self._stop.is_set():
                return None
        started = self._timer()
        observed_at = self._clock()
        try:
            if observed_at.tzinfo is None or observed_at.utcoffset() is None:
                raise ValueError("dynamic discovery clock must be timezone-aware")
            result = runner.collect(
                breadth_per_source=self._breadth,
                observed_at=observed_at,
                session=self._session_source(observed_at),
                production_stage_source=self._comparison_source,
            )
            refresh = result.refresh
            with self._lock:
                self._refresh_count += 1
                if refresh is not None:
                    symbols = {row.symbol for row in refresh.rows}
                    overlap = sum(
                        1 for symbol in symbols if self._comparison_source(symbol)
                    )
                    self._provider_requests += refresh.request_count
                    self._provider_failures += len(refresh.failures)
                    self._rows_received += refresh.returned_row_count
                    self._unique_symbols = len(symbols)
                    self._production_overlap = overlap
                    self._shadow_only = len(symbols) - overlap
                elif result.failure_type is not None:
                    self._provider_failures += 1
                    self._last_error_type = result.failure_type
            metrics = self.metrics()
            _LOGGER.info(
                "dynamic momentum refresh: breadth=%d requests=%d failures=%d "
                "rows=%d unique=%d overlap=%d shadow_only=%d accepted=%d "
                "suppressed=%d promotions=%d queue=%d high_water=%d "
                "worker_lag_ms=%.3f memory_state_bytes=%d",
                metrics.breadth,
                0 if refresh is None else refresh.request_count,
                1 if refresh is None else len(refresh.failures),
                0 if refresh is None else refresh.returned_row_count,
                metrics.unique_symbols,
                metrics.production_overlap,
                metrics.shadow_only_symbols,
                metrics.episodes_accepted,
                metrics.episodes_suppressed,
                metrics.promotions,
                metrics.queue_depth,
                metrics.queue_high_water,
                metrics.maximum_worker_lag_ms,
                metrics.memory_state_size,
            )
            return result
        except Exception as exc:
            self._record_failure(exc)
            return None
        finally:
            elapsed = max(0.0, (self._timer() - started) * 1000.0)
            with self._lock:
                self._max_refresh_ms = max(self._max_refresh_ms, elapsed)

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        started = monotonic()
        self._stop.set()
        thread = self._thread
        scheduler_stopped = True
        if thread is not None:
            thread.join(max(0.0, timeout_seconds))
            scheduler_stopped = not thread.is_alive()
        remaining = max(0.0, timeout_seconds - (monotonic() - started))
        service = self._service
        worker_stopped = True
        if service is not None:
            try:
                worker_stopped = service.close(timeout_seconds=remaining)
            except Exception as exc:
                self._record_failure(exc)
                worker_stopped = False
        stopped = scheduler_stopped and worker_stopped
        with self._lock:
            self._running = False
            self._stopped = stopped
            if not stopped and self._last_error_type is None:
                self._last_error_type = "BoundedShutdownTimeout"
        return stopped

    def metrics(self) -> DynamicMomentumRuntimeMetrics:
        service = self._service
        service_metrics = service.metrics() if service is not None else None
        retained_bytes = (
            service.estimated_retained_bytes() if service is not None else 0
        ) + self._comparison_memory_source()
        with self._lock:
            return DynamicMomentumRuntimeMetrics(
                enabled=self.enabled,
                healthy=(
                    self._last_error_type is None
                    and self._provider_failures == 0
                    and (service_metrics is None or service_metrics.failed == 0)
                ),
                running=self._running,
                breadth=self._breadth,
                refresh_seconds=self._refresh_seconds,
                refresh_count=self._refresh_count,
                provider_requests=self._provider_requests,
                provider_failures=self._provider_failures,
                rows_received=self._rows_received,
                unique_symbols=self._unique_symbols,
                production_overlap=self._production_overlap,
                shadow_only_symbols=self._shadow_only,
                episodes_accepted=(0 if service_metrics is None else service_metrics.accepted),
                episodes_suppressed=(0 if service_metrics is None else service_metrics.suppressed),
                promotions=(0 if service_metrics is None else service_metrics.promotion_count),
                queue_depth=(0 if service_metrics is None else service_metrics.queue_depth),
                queue_high_water=(0 if service_metrics is None else service_metrics.queue_high_water),
                maximum_worker_lag_ms=(0.0 if service_metrics is None else service_metrics.maximum_worker_lag_ms),
                persistence_failures=(0 if service_metrics is None else service_metrics.persistence_failures),
                maximum_refresh_latency_ms=self._max_refresh_ms,
                maximum_producer_latency_ms=(0.0 if service_metrics is None else service_metrics.maximum_producer_latency_ms),
                retained_symbols=max(
                    0 if service_metrics is None else service_metrics.retained_symbols,
                    self._comparison_retained_source(),
                ),
                memory_state_size=retained_bytes,
                persistence_path=str(self.path),
                stopped=self._stopped,
                last_error_type=self._last_error_type,
                event_counts=tuple(
                    (event.value, count)
                    for event, count in (
                        () if service_metrics is None else service_metrics.event_counts
                    )
                ),
            )

    def _run(self) -> None:
        while not self._stop.is_set():
            self.refresh_once()
            if self._stop.wait(self._refresh_seconds):
                break
        with self._lock:
            self._running = False

    def _record_failure(self, exc: Exception) -> None:
        error_type = type(exc).__name__
        with self._lock:
            self._last_error_type = error_type
        _LOGGER.warning("dynamic momentum research degraded: %s", error_type)


__all__ = [
    "CollectionResult",
    "DynamicMomentumDiscoveryRunner",
    "DynamicMomentumDiscoveryRuntime",
    "DynamicMomentumRuntimeMetrics",
]
