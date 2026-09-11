"""Bounded, payload-free performance counters for the Atlas runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections import deque
from datetime import UTC, datetime
from contextlib import contextmanager
from threading import local, RLock
from time import monotonic
from typing import Any, Callable


_QUEUE_THRESHOLDS = (100, 500, 1000, 1500)
DiagnosticSink = Callable[[str, dict[str, object]], None]

_STARTUP_STAGES = (
    "process_started", "gui_ready", "runtime_started", "broker_connect_started",
    "broker_connected", "stream_connect_started", "transport_connected", "registration_ready", "universe_refresh_started",
    "universe_refresh_completed", "reference_warmup_started",
    "reference_warmup_completed", "subscription_requested",
    "subscription_completed", "first_raw_callback", "first_callback_dequeued",
    "first_payload_decode_attempt", "first_payload_decode_success",
    "first_normalized_market_event", "first_scanner_ingestion",
    "first_scanner_evaluation", "scanner_active", "feed_healthy", "stale_detected",
    "reconnect_started", "reconnect_completed", "first_fresh_payload_after_reconnect",
)

_STARTUP_COUNTERS = (
    "reference_warmup_symbols_total", "reference_warmup_symbols_completed",
    "reference_warmup_symbols_accepted", "reference_warmup_symbols_rejected",
    "raw_callbacks_received", "callbacks_enqueued", "callbacks_dequeued",
    "decode_attempts", "decode_successes", "decode_failures", "decode_ignored",
    "normalized_market_events_emitted", "scanner_market_events_received",
    "scanner_evaluations",
    "subscription_requested_symbols",
    "subscription_completed_symbols",
    "subscription_batch_count",
    "unique_symbols_observed_before_feed_healthy",
)

_STARTUP_DURATION_PAIRS = {
    "runtime_start_to_broker_connect_ms": ("runtime_started", "broker_connect_started"),
    "broker_connect_ms": ("broker_connect_started", "broker_connected"),
    "transport_to_registration_ms": ("transport_connected", "registration_ready"),
    "registration_to_refresh_start_ms": ("registration_ready", "universe_refresh_started"),
    "universe_refresh_duration_ms": ("universe_refresh_started", "universe_refresh_completed"),
    "reference_warmup_duration_ms": ("reference_warmup_started", "reference_warmup_completed"),
    "subscription_duration_ms": ("subscription_requested", "subscription_completed"),
    "subscription_to_first_raw_callback_ms": ("subscription_completed", "first_raw_callback"),
    "raw_callback_to_first_dequeue_ms": ("first_raw_callback", "first_callback_dequeued"),
    "first_dequeue_to_first_decode_success_ms": ("first_callback_dequeued", "first_payload_decode_success"),
    "decode_success_to_first_normalized_event_ms": ("first_payload_decode_success", "first_normalized_market_event"),
    "normalized_event_to_first_scanner_ingestion_ms": ("first_normalized_market_event", "first_scanner_ingestion"),
    "scanner_ingestion_to_first_evaluation_ms": ("first_scanner_ingestion", "first_scanner_evaluation"),
    "runtime_start_to_scanner_ready_ms": ("runtime_started", "subscription_completed"),
    "stream_connect_ms": ("stream_connect_started", "transport_connected"),
    "subscription_to_first_payload_ms": ("subscription_completed", "first_normalized_market_event"),
}

_KNOWN_EVENT_TYPE_NAMES = frozenset(
    {
        "OrdersUpdated",
        "PositionsUpdated",
        "BrokerAccountUpdated",
        "WatchlistUpdated",
        "HealthUpdated",
        "PortfolioUpdated",
        "PortfolioIntelligenceUpdated",
        "PortfolioObservationPublished",
        "DecisionsUpdated",
        "TimelineUpdated",
        "RuntimeStarting",
        "RuntimeStarted",
        "RuntimeCycleCompleted",
        "PaperRuntimeUpdated",
        "RuntimeStopping",
        "RuntimeStopped",
        "RuntimeFailed",
    }
)


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
    discovery_cycles: int = 0
    discovery_detector_evaluations: int = 0
    discovery_raw_firings: int = 0
    discovery_unique_episodes: int = 0
    discovery_normalized_opportunities: int = 0
    discovery_strategy_memberships: int = 0
    discovery_strategy_transitions: int = 0
    discovery_position_correlations: int = 0
    discovery_thesis_observations: int = 0
    discovery_add_on_candidates: int = 0
    discovery_market_observations: int = 0
    discovery_completed_bars: int = 0
    discovery_callback_build_p50_ms: float = 0.0
    discovery_callback_build_p90_ms: float = 0.0
    discovery_callback_build_p99_ms: float = 0.0
    discovery_callback_build_max_ms: float = 0.0
    discovery_strategy_coverage: tuple[str, ...] = ()
    component_timings: dict[str, dict[str, object]] = field(default_factory=dict)


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
        self._component_samples: dict[str, deque[float]] = {}
        self._component_calls: dict[str, int] = {}
        self._component_totals: dict[str, float] = {}
        self._component_latest: dict[str, float] = {}
        self._component_max: dict[str, float] = {}
        self._component_slow: dict[str, int] = {}
        self._component_failures: dict[str, int] = {}
        self._component_last_diagnostic: dict[str, float] = {}
        self._queue_thresholds_above: set[int] = set()
        self._diagnostic_sink: DiagnosticSink | None = None
        self._trace_local = local()
        self._forensic_counters: dict[str, int] = {
            "scanner_related_events_emitted": 0,
            "scanner_related_state_revisions": 0,
            "operations_bus_root_publications": 0,
            "operations_bus_derived_publications": 0,
            "operations_bus_max_cascade_depth": 0,
        }
        self._cascade_publications_by_depth = {
            f"depth_{depth}": 0 for depth in range(1, 9)
        }
        self._cascade_publications_by_depth["depth_overflow"] = 0
        self._derived_publications_by_root = {
            name: 0 for name in _KNOWN_EVENT_TYPE_NAMES
        }
        self._derived_publications_by_root["UNKNOWN"] = 0
        self._startup_stage_timestamps: dict[str, str | None] = {
            stage: None for stage in _STARTUP_STAGES
        }
        self._startup_stage_monotonic: dict[str, float | None] = {
            stage: None for stage in _STARTUP_STAGES
        }
        self._startup_counters = {
            counter: 0 for counter in _STARTUP_COUNTERS
        }
        self._startup_current_reference_symbol: str | None = None
        self._startup_observed_symbols: set[str] = set()
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
            "discovery_cycles": 0,
            "discovery_detector_evaluations": 0,
            "discovery_raw_firings": 0,
            "discovery_unique_episodes": 0,
            "discovery_normalized_opportunities": 0,
            "discovery_strategy_memberships": 0,
            "discovery_strategy_transitions": 0,
            "discovery_position_correlations": 0,
            "discovery_thesis_observations": 0,
            "discovery_add_on_candidates": 0,
            "discovery_market_observations": 0,
            "discovery_completed_bars": 0,
            "discovery_callback_build_p50_ms": 0.0,
            "discovery_callback_build_p90_ms": 0.0,
            "discovery_callback_build_p99_ms": 0.0,
            "discovery_callback_build_max_ms": 0.0,
            "discovery_strategy_coverage": (),
        }

    def increment(self, name: str, amount: int = 1) -> None:
        if name not in self._counters:
            raise KeyError(name)
        if amount < 0:
            raise ValueError("performance counter increment cannot be negative")
        with self._lock:
            self._counters[name] += amount

    @contextmanager
    def operations_publication(self, event_type: str):
        """Track one synchronous publication without retaining payloads.

        Publication depth is one-based: the root publication is depth 1 and
        each nested synchronous publication increments the depth by one.
        """
        bucket = _event_type_bucket(event_type)
        prior_depth = getattr(self._trace_local, "cascade_depth", 0)
        prior_root = getattr(self._trace_local, "cascade_root", None)
        depth = prior_depth + 1
        root = bucket if prior_depth == 0 else prior_root or "UNKNOWN"
        with self._lock:
            if prior_depth == 0:
                self._forensic_counters["operations_bus_root_publications"] += 1
            else:
                self._forensic_counters["operations_bus_derived_publications"] += 1
                self._derived_publications_by_root[root] += 1
            self._forensic_counters["operations_bus_max_cascade_depth"] = max(
                self._forensic_counters["operations_bus_max_cascade_depth"], depth
            )
            depth_key = f"depth_{depth}" if depth <= 8 else "depth_overflow"
            self._cascade_publications_by_depth[depth_key] += 1
        self._trace_local.cascade_depth = depth
        self._trace_local.cascade_root = root
        try:
            yield
        finally:
            self._trace_local.cascade_depth = prior_depth
            self._trace_local.cascade_root = prior_root

    def record_scanner_event_emitted(self) -> None:
        with self._lock:
            self._forensic_counters["scanner_related_events_emitted"] += 1

    def scanner_context_active(self) -> bool:
        return bool(getattr(self._trace_local, "scanner_event_depth", 0))

    @contextmanager
    def scanner_event_context(self):
        """Mark a scanner-originated synchronous event without retaining it."""
        prior = getattr(self._trace_local, "scanner_event_depth", 0)
        self._trace_local.scanner_event_depth = prior + 1
        try:
            yield
        finally:
            self._trace_local.scanner_event_depth = prior

    def record_scanner_state_revision(self) -> None:
        with self._lock:
            self._forensic_counters["scanner_related_state_revisions"] += 1

    def record_startup_stage(self, stage: str) -> None:
        """Record the first occurrence of one bounded startup stage."""
        if stage not in _STARTUP_STAGES:
            raise ValueError(f"unknown startup diagnostic stage: {stage}")
        with self._lock:
            if self._startup_stage_monotonic[stage] is None:
                self._startup_stage_monotonic[stage] = monotonic()
                self._startup_stage_timestamps[stage] = datetime.now(UTC).isoformat()

    def increment_startup_counter(self, name: str, amount: int = 1) -> None:
        """Increment a fixed startup counter without retaining runtime data."""
        if name not in _STARTUP_COUNTERS:
            raise ValueError(f"unknown startup diagnostic counter: {name}")
        if amount < 0:
            raise ValueError("startup diagnostic increment cannot be negative")
        with self._lock:
            self._startup_counters[name] += amount

    def set_startup_reference_symbol(self, symbol: str | None) -> None:
        with self._lock:
            self._startup_current_reference_symbol = (
                None if symbol is None else str(symbol).strip().upper() or None
            )

    def record_startup_symbol(self, symbol: str | None) -> None:
        """Track unique symbols observed during startup with a hard bound."""
        normalized = None if symbol is None else str(symbol).strip().upper()
        if not normalized:
            return
        with self._lock:
            if self._startup_stage_monotonic["feed_healthy"] is not None:
                return
            if len(self._startup_observed_symbols) >= 256:
                return
            self._startup_observed_symbols.add(normalized)
            self._startup_counters[
                "unique_symbols_observed_before_feed_healthy"
            ] = len(self._startup_observed_symbols)

    def startup_metrics(self) -> dict[str, object]:
        """Return bounded startup timestamps, durations, and counters."""
        with self._lock:
            values: dict[str, object] = {
                f"{stage}_at": self._startup_stage_timestamps[stage]
                for stage in _STARTUP_STAGES
            }
            for name, (start, end) in _STARTUP_DURATION_PAIRS.items():
                started = self._startup_stage_monotonic[start]
                finished = self._startup_stage_monotonic[end]
                values[name] = (
                    None if started is None or finished is None
                    else round((finished - started) * 1000.0, 3)
                )
            values.update(self._startup_counters)
            values["reference_warmup_current_symbol"] = (
                self._startup_current_reference_symbol
            )
            return values

    def forensic_metrics(self) -> dict[str, int]:
        """Return bounded forensic counters for periodic observability."""
        with self._lock:
            values = dict(self._forensic_counters)
            values.update(
                {
                    f"operations_bus_publications_{key}": value
                    for key, value in self._cascade_publications_by_depth.items()
                }
            )
            values.update(
                {
                    f"operations_bus_derived_from_{key}": value
                    for key, value in self._derived_publications_by_root.items()
                }
            )
            values.update(
                {
                    "scanner_snapshots_generated": self._counters[
                        "scanner_snapshots_generated"
                    ],
                    "scanner_snapshots_published": self._counters[
                        "scanner_snapshots_published"
                    ],
                    "scanner_snapshots_suppressed": self._counters[
                        "scanner_snapshots_suppressed_unchanged"
                    ],
                }
            )
            return values

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

    def record_component_duration(
        self,
        component: str,
        duration_ms: float,
        *,
        event_type: str | None = None,
        symbol: str | None = None,
        success: bool = True,
        slow_threshold_ms: float = 1000.0,
    ) -> None:
        """Record bounded component timing and rate-limit slow diagnostics."""
        if not isinstance(component, str) or not component.strip():
            raise ValueError("component must be non-empty text")
        if duration_ms < 0 or slow_threshold_ms <= 0:
            raise ValueError("component timing values are invalid")
        key = component.strip()
        now = monotonic()
        diagnostic: dict[str, object] | None = None
        with self._lock:
            samples = self._component_samples.setdefault(key, deque(maxlen=256))
            samples.append(duration_ms)
            self._component_calls[key] = self._component_calls.get(key, 0) + 1
            self._component_totals[key] = self._component_totals.get(key, 0.0) + duration_ms
            self._component_latest[key] = duration_ms
            self._component_max[key] = max(self._component_max.get(key, 0.0), duration_ms)
            if not success:
                self._component_failures[key] = self._component_failures.get(key, 0) + 1
            if duration_ms >= slow_threshold_ms:
                self._component_slow[key] = self._component_slow.get(key, 0) + 1
                last = self._component_last_diagnostic.get(key, 0.0)
                if now - last >= 5.0:
                    self._component_last_diagnostic[key] = now
                    diagnostic = {
                        "component": key,
                        "event_type": event_type,
                        "symbol": symbol,
                        "duration_ms": round(duration_ms, 3),
                        "success": bool(success),
                    }
        if diagnostic is not None:
            self._emit_diagnostic("slow_component", diagnostic)

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
            "discovery_cycles": "discovery_cycles",
            "discovery_detector_evaluations": "discovery_detector_evaluations",
            "discovery_raw_firings": "discovery_raw_firings",
            "discovery_unique_episodes": "discovery_unique_episodes",
            "discovery_normalized_opportunities": "discovery_normalized_opportunities",
            "discovery_strategy_memberships": "discovery_strategy_memberships",
            "discovery_strategy_transitions": "discovery_strategy_transitions",
            "discovery_position_correlations": "discovery_position_correlations",
            "discovery_thesis_observations": "discovery_thesis_observations",
            "discovery_add_on_candidates": "discovery_add_on_candidates",
        }
        values = {name: int(getattr(metrics, field, 0)) for name, field in mapping.items()}
        with self._lock:
            self._trade_intelligence.update(values)

    def set_trade_intelligence_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._trade_intelligence["trade_intelligence_enabled"] = bool(enabled)

    def update_discovery(self, telemetry: object) -> None:
        mapping = {
            "discovery_market_observations": "market_observations",
            "discovery_completed_bars": "completed_bars",
            "discovery_callback_build_p50_ms": "callback_build_p50_ms",
            "discovery_callback_build_p90_ms": "callback_build_p90_ms",
            "discovery_callback_build_p99_ms": "callback_build_p99_ms",
            "discovery_callback_build_max_ms": "callback_build_max_ms",
        }
        values = {name: float(getattr(telemetry, field, 0)) for name, field in mapping.items()}
        values["discovery_market_observations"] = int(values["discovery_market_observations"])
        values["discovery_completed_bars"] = int(values["discovery_completed_bars"])
        values["discovery_strategy_coverage"] = tuple(
            f"{item.strategy_id}:{item.evaluations}/{item.raw_detections}/"
            f"{item.unique_episodes}/{item.normalized_opportunities}"
            for item in getattr(telemetry, "coverage", ())
        )
        with self._lock:
            self._trade_intelligence.update(values)

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
            component_timings = {}
            for name, samples in self._component_samples.items():
                ordered = sorted(samples)
                component_timings[name] = {
                    "calls": self._component_calls.get(name, 0),
                    "total_ms": round(self._component_totals.get(name, 0.0), 3),
                    "latest_ms": round(self._component_latest.get(name, 0.0), 3),
                    "max_ms": round(self._component_max.get(name, 0.0), 3),
                    "p50_ms": round(_percentile(ordered, 0.50), 3),
                    "p90_ms": round(_percentile(ordered, 0.90), 3),
                    "p99_ms": round(_percentile(ordered, 0.99), 3),
                    "slow_calls": self._component_slow.get(name, 0),
                    "failures": self._component_failures.get(name, 0),
                }
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
                component_timings=component_timings,
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


def _event_type_bucket(event_type: str) -> str:
    return event_type if event_type in _KNOWN_EVENT_TYPE_NAMES else "UNKNOWN"


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
