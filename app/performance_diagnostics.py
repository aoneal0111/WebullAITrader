"""Bounded, payload-free performance counters for the Atlas runtime."""

from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from datetime import UTC, datetime
from threading import local, RLock
from time import monotonic
from typing import Any, Callable


_QUEUE_THRESHOLDS = (100, 500, 1000, 1500)
DiagnosticSink = Callable[[str, dict[str, object]], None]


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
    scanner_duration_ms: float = 0.0
    experiment_capture_duration_max_ms: float = 0.0
    experiment_capture_duration_ms: float = 0.0
    observer_duration_max_ms: float = 0.0
    observer_duration_ms: float = 0.0
    completed_bar_flush_duration_ms: float = 0.0
    completed_bar_flush_duration_max_ms: float = 0.0
    report_request_duration_ms: float = 0.0
    report_request_duration_max_ms: float = 0.0
    report_build_duration_ms: float = 0.0
    report_build_duration_max_ms: float = 0.0
    projection_duration_ms: float = 0.0
    projection_duration_max_ms: float = 0.0
    research_queue_depth: int = 0
    research_queue_high_water: int = 0
    research_worker_lag_max_ms: float = 0.0
    research_events_enqueued: int = 0
    research_events_completed: int = 0
    research_events_rejected: int = 0
    research_failures: int = 0
    processing_delayed_events: int = 0
    report_refresh_failures: int = 0
    latency_diagnostics_persisted: int = 0
    callback_threshold_events: int = 0
    trade_intelligence_enabled: bool = False
    trade_intelligence_experiences_created: int = 0
    trade_intelligence_decisions_recorded: int = 0
    trade_intelligence_outcomes_completed: int = 0
    trade_intelligence_profitable_misses: int = 0
    trade_intelligence_protected_rejections: int = 0
    trade_intelligence_queue_depth: int = 0
    trade_intelligence_queue_high_water: int = 0
    trade_intelligence_accepted: int = 0
    trade_intelligence_completed: int = 0
    trade_intelligence_failed: int = 0
    trade_intelligence_rejected: int = 0
    trade_intelligence_worker_lag_max_ms: int = 0
    trade_intelligence_worker_lag_p50_ms: int = 0
    trade_intelligence_worker_lag_p90_ms: int = 0
    trade_intelligence_worker_lag_p99_ms: int = 0
    trade_intelligence_pressure_episodes: int = 0
    trade_intelligence_recovery_episodes: int = 0
    trade_intelligence_rejections: int = 0
    trade_intelligence_failures: int = 0
    trade_intelligence_outstanding: int = 0


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
            "report_refresh_failures": 0,
            "latency_diagnostics_persisted": 0,
            "callback_threshold_events": 0,
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
        self._scanner_duration_ms = 0.0
        self._experiment_capture_duration_max_ms = 0.0
        self._experiment_capture_duration_ms = 0.0
        self._observer_duration_max_ms = 0.0
        self._observer_duration_ms = 0.0
        self._completed_bar_flush_duration_ms = 0.0
        self._completed_bar_flush_duration_max_ms = 0.0
        self._report_request_duration_ms = 0.0
        self._report_request_duration_max_ms = 0.0
        self._report_build_duration_ms = 0.0
        self._report_build_duration_max_ms = 0.0
        self._projection_duration_ms = 0.0
        self._projection_duration_max_ms = 0.0
        self._research_worker_lag_max_ms = 0.0
        self._arrival_started_at: float | None = None
        self._arrival_latest_at: float | None = None
        self._processing_started_at: float | None = None
        self._processing_latest_at: float | None = None
        self._processing_count = 0
        self._queue_thresholds_above: set[int] = set()
        self._diagnostic_sink: DiagnosticSink | None = None
        self._trace_local = local()
        self._trade_intelligence = {
            "trade_intelligence_enabled": False,
            "trade_intelligence_experiences_created": 0,
            "trade_intelligence_decisions_recorded": 0,
            "trade_intelligence_outcomes_completed": 0,
            "trade_intelligence_profitable_misses": 0,
            "trade_intelligence_protected_rejections": 0,
            "trade_intelligence_queue_depth": 0,
            "trade_intelligence_queue_high_water": 0,
            "trade_intelligence_accepted": 0,
            "trade_intelligence_completed": 0,
            "trade_intelligence_failed": 0,
            "trade_intelligence_rejected": 0,
            "trade_intelligence_worker_lag_p50_ms": 0,
            "trade_intelligence_worker_lag_p90_ms": 0,
            "trade_intelligence_worker_lag_p99_ms": 0,
            "trade_intelligence_worker_lag_max_ms": 0,
            "trade_intelligence_pressure_episodes": 0,
            "trade_intelligence_recovery_episodes": 0,
            "trade_intelligence_rejections": 0,
            "trade_intelligence_failures": 0,
            "trade_intelligence_outstanding": 0,
        }

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
            events = self._queue_crossings_locked(queue_depth)
        self._emit_diagnostics(events)

    def set_callback_queue_depth(self, value: int) -> None:
        if value < 0:
            raise ValueError("callback queue depth cannot be negative")
        with self._lock:
            self._callback_queue_depth = value
            self._callback_queue_high_water = max(
                self._callback_queue_high_water, value
            )
            events = self._queue_crossings_locked(value)
        self._emit_diagnostics(events)

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
        self._record_latest_and_maximum(
            "_scanner_duration_ms", "_scanner_duration_max_ms", duration_ms
        )

    def record_experiment_capture_duration(self, duration_ms: float) -> None:
        self._record_latest_and_maximum(
            "_experiment_capture_duration_ms",
            "_experiment_capture_duration_max_ms",
            duration_ms,
        )

    def record_observer_duration(self, duration_ms: float) -> None:
        self._record_latest_and_maximum(
            "_observer_duration_ms", "_observer_duration_max_ms", duration_ms
        )

    def record_completed_bar_flush_duration(self, duration_ms: float) -> None:
        self._record_latest_and_maximum(
            "_completed_bar_flush_duration_ms",
            "_completed_bar_flush_duration_max_ms",
            duration_ms,
        )

    def record_report_request_duration(self, duration_ms: float) -> None:
        self._record_latest_and_maximum(
            "_report_request_duration_ms",
            "_report_request_duration_max_ms",
            duration_ms,
        )

    def record_report_build_duration(self, duration_ms: float) -> None:
        self._record_latest_and_maximum(
            "_report_build_duration_ms",
            "_report_build_duration_max_ms",
            duration_ms,
        )

    def record_projection_duration(self, duration_ms: float) -> None:
        self._record_latest_and_maximum(
            "_projection_duration_ms", "_projection_duration_max_ms", duration_ms
        )

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

    def update_trade_intelligence(self, metrics: object) -> None:
        """Publish one bounded in-memory worker snapshot; no database query occurs."""
        mapping = {
            "trade_intelligence_experiences_created": "experiences_created",
            "trade_intelligence_decisions_recorded": "decisions_recorded",
            "trade_intelligence_outcomes_completed": "outcomes_completed",
            "trade_intelligence_profitable_misses": "profitable_misses",
            "trade_intelligence_protected_rejections": "protected_rejections",
            "trade_intelligence_queue_depth": "queue_depth",
            "trade_intelligence_queue_high_water": "queue_high_water",
            "trade_intelligence_accepted": "accepted",
            "trade_intelligence_completed": "completed",
            "trade_intelligence_failed": "failed",
            "trade_intelligence_rejected": "rejected",
            "trade_intelligence_worker_lag_p50_ms": "worker_lag_p50_ms",
            "trade_intelligence_worker_lag_p90_ms": "worker_lag_p90_ms",
            "trade_intelligence_worker_lag_p99_ms": "worker_lag_p99_ms",
            "trade_intelligence_worker_lag_max_ms": "worker_lag_max_ms",
            "trade_intelligence_pressure_episodes": "pressure_episodes",
            "trade_intelligence_recovery_episodes": "pressure_recoveries",
            "trade_intelligence_rejections": "rejected",
            "trade_intelligence_failures": "failed",
            "trade_intelligence_outstanding": "outstanding",
        }
        values = {name: int(getattr(metrics, field, 0)) for name, field in mapping.items()}
        with self._lock:
            self._trade_intelligence.update(values)

    def set_trade_intelligence_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._trade_intelligence["trade_intelligence_enabled"] = bool(enabled)

    def _record_maximum(self, name: str, value: float) -> None:
        if value < 0:
            raise ValueError("performance duration cannot be negative")
        with self._lock:
            setattr(self, name, max(getattr(self, name), value))

    def _record_latest_and_maximum(
        self, latest_name: str, maximum_name: str, value: float
    ) -> None:
        if value < 0:
            raise ValueError("performance duration cannot be negative")
        with self._lock:
            setattr(self, latest_name, value)
            setattr(self, maximum_name, max(getattr(self, maximum_name), value))

    def set_diagnostic_sink(self, sink: DiagnosticSink | None) -> None:
        if sink is not None and not callable(sink):
            raise TypeError("diagnostic sink must be callable or None")
        with self._lock:
            self._diagnostic_sink = sink

    def begin_latency_trace(self, event: Any, scanner_started_at: datetime) -> None:
        with self._lock:
            queue_depth = self._callback_queue_depth
            queue_high_water = self._callback_queue_high_water
        self._trace_local.value = {
            "source": str(getattr(event, "source", "unknown")),
            "sequence": getattr(event, "sequence", None),
            "symbol": getattr(event, "symbol", None),
            "event_type": getattr(getattr(event, "event_type", None), "value", None),
            "provider_timestamp": _iso(getattr(event, "timestamp", None)),
            "callback_received_at": _iso(
                getattr(event, "received_timestamp", None)
            ),
            "dequeued_at": _iso(getattr(event, "dequeued_timestamp", None)),
            "scanner_started_at": _iso(scanner_started_at),
            "callback_queue_depth_at_dequeue": queue_depth,
            "callback_queue_high_water": queue_high_water,
        }

    def mark_latency_trace_timestamp(self, name: str, value: datetime) -> None:
        trace = getattr(self._trace_local, "value", None)
        if trace is not None:
            trace[name] = _iso(value)

    def mark_latency_trace_stage(self, name: str, value: object) -> None:
        trace = getattr(self._trace_local, "value", None)
        if trace is not None:
            trace[name] = value

    def record_execution_safety(
        self,
        *,
        processing_delayed: bool,
        entry_authorized: bool,
        execution_quote_requested: bool,
        paper_order_created: bool,
    ) -> None:
        trace = getattr(self._trace_local, "value", None)
        if trace is not None:
            trace["execution_safety"] = {
                "processing_delayed": processing_delayed,
                "entry_authorized": entry_authorized,
                "execution_quote_requested": (
                    execution_quote_requested
                    or bool(trace.get("execution_quote_requested"))
                ),
                "paper_order_created": paper_order_created,
            }

    def finish_latency_trace(self, finished_at: datetime) -> None:
        trace = getattr(self._trace_local, "value", None)
        if trace is None:
            return
        self._trace_local.value = None
        trace["observer_ended_at"] = _iso(finished_at)
        received = _parse_iso(trace.get("callback_received_at"))
        source = _parse_iso(trace.get("provider_timestamp"))
        processing_age_ms = _age_ms(received, finished_at)
        delivery_age_ms = _age_ms(source, finished_at)
        trace["total_processing_age_ms"] = processing_age_ms
        trace["source_delivery_age_ms"] = delivery_age_ms
        safety = trace.get("execution_safety")
        delayed_by_authority = bool(
            isinstance(safety, dict) and safety.get("processing_delayed")
        )
        if (
            processing_age_ms > 5_000.0
            or delivery_age_ms > 5_000.0
            or delayed_by_authority
        ):
            trace["recorded_at"] = _iso(finished_at)
            with self._lock:
                trace["callback_queue_high_water"] = self._callback_queue_high_water
                trace["stage_durations"] = {
                    "scanner_current_ms": self._scanner_duration_ms,
                    "scanner_max_ms": self._scanner_duration_max_ms,
                    "experiment_enqueue_current_ms": (
                        self._experiment_capture_duration_ms
                    ),
                    "experiment_enqueue_max_ms": (
                        self._experiment_capture_duration_max_ms
                    ),
                    "observer_current_ms": self._observer_duration_ms,
                    "observer_max_ms": self._observer_duration_max_ms,
                    "completed_bar_flush_current_ms": (
                        self._completed_bar_flush_duration_ms
                    ),
                    "completed_bar_flush_max_ms": (
                        self._completed_bar_flush_duration_max_ms
                    ),
                    "report_request_current_ms": self._report_request_duration_ms,
                    "report_request_max_ms": self._report_request_duration_max_ms,
                    "report_build_current_ms": self._report_build_duration_ms,
                    "report_build_max_ms": self._report_build_duration_max_ms,
                    "projection_current_ms": self._projection_duration_ms,
                    "projection_max_ms": self._projection_duration_max_ms,
                }
            self._emit_diagnostic("market_latency_abnormal", trace)

    def emit_runtime_diagnostic(
        self, kind: str, payload: dict[str, object]
    ) -> None:
        material = dict(payload)
        material.setdefault("recorded_at", datetime.now(UTC).isoformat())
        self._emit_diagnostic(kind, material)

    def _queue_crossings_locked(
        self, depth: int
    ) -> tuple[tuple[str, dict[str, object]], ...]:
        events: list[tuple[str, dict[str, object]]] = []
        for threshold in _QUEUE_THRESHOLDS:
            above = threshold in self._queue_thresholds_above
            if depth >= threshold and not above:
                self._queue_thresholds_above.add(threshold)
                direction = "CROSSED_UP"
            elif depth < threshold and above:
                self._queue_thresholds_above.remove(threshold)
                direction = "RECOVERED_BELOW"
            else:
                continue
            events.append(("callback_queue_threshold", {
                "recorded_at": datetime.now(UTC).isoformat(),
                "threshold": threshold,
                "direction": direction,
                "queue_depth": depth,
                "callback_queue_high_water": self._callback_queue_high_water,
            }))
        return tuple(events)

    def _emit_diagnostics(
        self, events: tuple[tuple[str, dict[str, object]], ...]
    ) -> None:
        for kind, payload in events:
            self._emit_diagnostic(kind, payload)

    def _emit_diagnostic(self, kind: str, payload: dict[str, object]) -> None:
        with self._lock:
            sink = self._diagnostic_sink
            if sink is None:
                return
        try:
            sink(kind, payload)
        except Exception:
            return
        with self._lock:
            counter = (
                "callback_threshold_events"
                if kind == "callback_queue_threshold"
                else "latency_diagnostics_persisted"
            )
            self._counters[counter] += 1

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
                **self._trade_intelligence,
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
                scanner_duration_ms=self._scanner_duration_ms,
                experiment_capture_duration_max_ms=(
                    self._experiment_capture_duration_max_ms
                ),
                experiment_capture_duration_ms=(
                    self._experiment_capture_duration_ms
                ),
                observer_duration_max_ms=self._observer_duration_max_ms,
                observer_duration_ms=self._observer_duration_ms,
                completed_bar_flush_duration_ms=(
                    self._completed_bar_flush_duration_ms
                ),
                completed_bar_flush_duration_max_ms=(
                    self._completed_bar_flush_duration_max_ms
                ),
                report_request_duration_ms=self._report_request_duration_ms,
                report_request_duration_max_ms=(
                    self._report_request_duration_max_ms
                ),
                report_build_duration_ms=self._report_build_duration_ms,
                report_build_duration_max_ms=self._report_build_duration_max_ms,
                projection_duration_ms=self._projection_duration_ms,
                projection_duration_max_ms=self._projection_duration_max_ms,
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


def _iso(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _age_ms(start: datetime | None, end: datetime) -> float:
    if start is None:
        return 0.0
    return max(0.0, (end - start).total_seconds() * 1000.0)


__all__ = [
    "PerformanceDiagnostics",
    "PerformanceSnapshot",
    "performance_diagnostics",
]
