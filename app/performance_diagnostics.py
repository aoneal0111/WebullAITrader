"""Bounded, payload-free performance counters for the Atlas runtime."""

from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from threading import RLock
from time import monotonic


@dataclass(frozen=True, slots=True)
class PerformanceSnapshot:
    gui_refresh_count: int = 0
    gui_refresh_hz: float = 0.0
    gui_refresh_duration_ms: float = 0.0
    gui_refresh_duration_avg_ms: float = 0.0
    gui_refresh_duration_max_ms: float = 0.0
    pending_gui_updates: int = 0
    maximum_pending_gui_updates: int = 0
    market_events_received: int = 0
    market_events_processed: int = 0
    scanner_evaluations: int = 0
    scanner_snapshots_generated: int = 0
    scanner_snapshots_published: int = 0
    scanner_snapshots_suppressed_unchanged: int = 0
    stale_events_skipped: int = 0
    event_store_rows_added: int = 0
    market_event_callbacks: int = 0
    callback_queue_depth: int = 0
    callback_queue_high_water: int = 0
    market_arrival_rate_hz: float = 0.0
    market_processing_rate_hz: float = 0.0
    event_processing_age_p50_ms: float = 0.0
    event_processing_age_p90_ms: float = 0.0
    event_processing_age_p99_ms: float = 0.0
    event_processing_age_max_ms: float = 0.0
    scanner_duration_max_ms: float = 0.0
    experiment_capture_duration_max_ms: float = 0.0
    observer_duration_max_ms: float = 0.0
    research_queue_depth: int = 0
    research_queue_high_water: int = 0
    research_worker_lag_max_ms: float = 0.0
    research_events_enqueued: int = 0
    research_events_completed: int = 0
    research_events_rejected: int = 0
    research_failures: int = 0
    processing_delayed_events: int = 0


class PerformanceDiagnostics:
    """Thread-safe counters whose storage remains constant under sustained load."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._counters: dict[str, int] = {
            "gui_refresh_count": 0,
            "market_events_received": 0,
            "market_events_processed": 0,
            "scanner_evaluations": 0,
            "scanner_snapshots_generated": 0,
            "scanner_snapshots_published": 0,
            "scanner_snapshots_suppressed_unchanged": 0,
            "stale_events_skipped": 0,
            "event_store_rows_added": 0,
            "market_event_callbacks": 0,
            "research_events_enqueued": 0,
            "research_events_completed": 0,
            "research_events_rejected": 0,
            "research_failures": 0,
            "processing_delayed_events": 0,
        }
        self._pending_gui_updates = 0
        self._maximum_pending_gui_updates = 0
        self._gui_duration_total_ms = 0.0
        self._gui_duration_max_ms = 0.0
        self._gui_duration_latest_ms = 0.0
        self._gui_interval_seconds = 0.0
        self._callback_queue_depth = 0
        self._callback_queue_high_water = 0
        self._research_queue_depth = 0
        self._research_queue_high_water = 0
        self._processing_ages_ms: deque[float] = deque(maxlen=2048)
        self._processing_age_max_ms = 0.0
        self._scanner_duration_max_ms = 0.0
        self._experiment_capture_duration_max_ms = 0.0
        self._observer_duration_max_ms = 0.0
        self._research_worker_lag_max_ms = 0.0
        self._arrival_started_at: float | None = None
        self._arrival_latest_at: float | None = None
        self._processing_started_at: float | None = None
        self._processing_latest_at: float | None = None
        self._processing_count = 0

    def increment(self, name: str, amount: int = 1) -> None:
        if name not in self._counters:
            raise KeyError(name)
        if amount < 0:
            raise ValueError("performance counter increment cannot be negative")
        with self._lock:
            self._counters[name] += amount

    def set_pending_gui_updates(self, value: int) -> None:
        if value < 0:
            raise ValueError("pending GUI update count cannot be negative")
        with self._lock:
            self._pending_gui_updates = value
            self._maximum_pending_gui_updates = max(
                self._maximum_pending_gui_updates, value
            )

    def record_market_event_callback(self, queue_depth: int) -> None:
        if queue_depth < 0:
            raise ValueError("callback queue depth cannot be negative")
        now = monotonic()
        with self._lock:
            self._counters["market_event_callbacks"] += 1
            self._callback_queue_depth = queue_depth
            self._callback_queue_high_water = max(
                self._callback_queue_high_water, queue_depth
            )
            self._arrival_started_at = self._arrival_started_at or now
            self._arrival_latest_at = now

    def set_callback_queue_depth(self, value: int) -> None:
        if value < 0:
            raise ValueError("callback queue depth cannot be negative")
        with self._lock:
            self._callback_queue_depth = value
            self._callback_queue_high_water = max(
                self._callback_queue_high_water, value
            )

    def record_event_processing_age(self, age_ms: float) -> None:
        if age_ms < 0:
            raise ValueError("event processing age cannot be negative")
        now = monotonic()
        with self._lock:
            self._processing_ages_ms.append(age_ms)
            self._processing_count += 1
            self._processing_age_max_ms = max(self._processing_age_max_ms, age_ms)
            self._processing_started_at = self._processing_started_at or now
            self._processing_latest_at = now

    def record_scanner_duration(self, duration_ms: float) -> None:
        self._record_maximum("_scanner_duration_max_ms", duration_ms)

    def record_experiment_capture_duration(self, duration_ms: float) -> None:
        self._record_maximum("_experiment_capture_duration_max_ms", duration_ms)

    def record_observer_duration(self, duration_ms: float) -> None:
        self._record_maximum("_observer_duration_max_ms", duration_ms)

    def set_research_queue_depth(self, value: int) -> None:
        if value < 0:
            raise ValueError("research queue depth cannot be negative")
        with self._lock:
            self._research_queue_depth = value
            self._research_queue_high_water = max(
                self._research_queue_high_water, value
            )

    def record_research_worker_lag(self, lag_ms: float) -> None:
        self._record_maximum("_research_worker_lag_max_ms", lag_ms)

    def _record_maximum(self, name: str, value: float) -> None:
        if value < 0:
            raise ValueError("performance duration cannot be negative")
        with self._lock:
            setattr(self, name, max(getattr(self, name), value))

    def record_gui_refresh(self, duration_ms: float, interval_seconds: float) -> None:
        if duration_ms < 0 or interval_seconds < 0:
            raise ValueError("GUI timing measurements cannot be negative")
        with self._lock:
            self._counters["gui_refresh_count"] += 1
            self._gui_duration_latest_ms = duration_ms
            self._gui_duration_total_ms += duration_ms
            self._gui_duration_max_ms = max(self._gui_duration_max_ms, duration_ms)
            self._gui_interval_seconds += interval_seconds

    def snapshot(self) -> PerformanceSnapshot:
        with self._lock:
            count = self._counters["gui_refresh_count"]
            values = dict(self._counters)
            ages = sorted(self._processing_ages_ms)
            return PerformanceSnapshot(
                **values,
                gui_refresh_hz=(
                    count / self._gui_interval_seconds
                    if self._gui_interval_seconds > 0
                    else 0.0
                ),
                gui_refresh_duration_ms=self._gui_duration_latest_ms,
                gui_refresh_duration_avg_ms=(
                    self._gui_duration_total_ms / count if count else 0.0
                ),
                gui_refresh_duration_max_ms=self._gui_duration_max_ms,
                pending_gui_updates=self._pending_gui_updates,
                maximum_pending_gui_updates=self._maximum_pending_gui_updates,
                callback_queue_depth=self._callback_queue_depth,
                callback_queue_high_water=self._callback_queue_high_water,
                market_arrival_rate_hz=_rate(
                    self._counters["market_event_callbacks"],
                    self._arrival_started_at,
                    self._arrival_latest_at,
                ),
                market_processing_rate_hz=_rate(
                    self._processing_count,
                    self._processing_started_at,
                    self._processing_latest_at,
                ),
                event_processing_age_p50_ms=_percentile(ages, 0.50),
                event_processing_age_p90_ms=_percentile(ages, 0.90),
                event_processing_age_p99_ms=_percentile(ages, 0.99),
                event_processing_age_max_ms=self._processing_age_max_ms,
                scanner_duration_max_ms=self._scanner_duration_max_ms,
                experiment_capture_duration_max_ms=(
                    self._experiment_capture_duration_max_ms
                ),
                observer_duration_max_ms=self._observer_duration_max_ms,
                research_queue_depth=self._research_queue_depth,
                research_queue_high_water=self._research_queue_high_water,
                research_worker_lag_max_ms=self._research_worker_lag_max_ms,
            )


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, int((len(values) - 1) * fraction)))
    return values[index]


def _rate(count: int, started: float | None, latest: float | None) -> float:
    if count < 2 or started is None or latest is None or latest <= started:
        return 0.0
    return (count - 1) / (latest - started)


performance_diagnostics = PerformanceDiagnostics()


__all__ = [
    "PerformanceDiagnostics",
    "PerformanceSnapshot",
    "performance_diagnostics",
]
