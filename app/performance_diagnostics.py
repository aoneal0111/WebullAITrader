"""Bounded, payload-free performance counters for the Atlas runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from collections import OrderedDict, deque
from datetime import UTC, datetime
from contextlib import contextmanager
import json
import os
import re
from pathlib import Path
from threading import Event, Thread, local, RLock
from time import monotonic
from typing import Any, Callable
from uuid import uuid4


_QUEUE_THRESHOLDS = (100, 500, 1000, 1500)
_DURABLE_SCHEMA_VERSION = 1
_DEFAULT_FLUSH_SECONDS = 15.0
_MAX_STREAM_FAILURE_SAMPLES = 16
_MAX_ENTRY_LIFECYCLE_RECORDS = 256
_MAX_ENTRY_PURSUIT_RECORDS = 512
_MAX_SETUP_TRANSITION_RECORDS = 512
_MAX_PROTECTION_EVENTS = 256
_MAX_STRATEGY_SELECTION_RECORDS = 256
_ENTRY_COUNTERS = (
    "entry_authorizations", "entry_orders_submitted", "pursuit_evaluations",
    "pursuit_not_invoked_due_to_state", "replacement_candidates",
    "replacements_approved", "replacements_submitted", "replacements_succeeded",
    "replacements_failed", "entry_expirations", "partial_fill_events",
    "post_partial_pursuit_evaluations", "new_lifecycle_authorizations_after_expiry",
    "same_lifecycle_suppression_after_expiry",
)
_PURSUIT_REASONS = frozenset({
    "REPLACE_APPROVED", "NO_PRICE_IMPROVEMENT", "REPRICE_INTERVAL",
    "MAX_REPLACEMENTS", "CHASE_CEILING", "SPREAD_TOO_WIDE",
    "LIQUIDITY_INSUFFICIENT", "QUOTE_STALE", "SIGNAL_STALE", "THESIS_INVALID",
    "OPPORTUNITY_QUALITY_REJECTED", "RISK_REJECTED", "ORDER_NOT_WORKING",
    "ORDER_TERMINAL", "POSITION_ALREADY_SATISFIED", "LIFECYCLE_SUPPRESSED",
    "MISSING_BID_ASK", "MISSING_DEPTH", "NO_REMAINING_QUANTITY",
    "CURRENT_QUOTE_UNAVAILABLE", "PREDECESSOR_NOT_FOUND",
    "PREDECESSOR_NOT_WORKING", "PREDECESSOR_NOT_LIMIT",
    "REPLACEMENT_LIMIT_EXHAUSTED", "INVALID_REPLACEMENT_SEQUENCE",
    "INVALID_CHASE_DEADLINE", "CHASE_WINDOW_EXPIRED", "REVALIDATION_REQUIRED",
    "STRUCTURAL_STOP_INVALID", "CANCELLATION_NOT_CONFIRMED",
    "PREDECESSOR_STATE_UNAVAILABLE", "REVALIDATION_FAILED",
    "POSITION_STATE_CONTRADICTS_FILLS", "INVALID_REPLACEMENT_INPUT",
    "INVALID_ACCOUNT_CONTEXT", "REPLACEMENT_SUBMISSION_FAILED",
    "REPLACEMENT_REJECTED", "REPLACEMENT_STATE_UNAVAILABLE",
    "REPLACEMENT_NOT_WORKING_LIMIT", "ORIGINAL_ENTRY_UNAVAILABLE",
    "PRICE_NOT_MOVED_ABOVE_PREDECESSOR", "INVALID_REPRICE_TIMESTAMP",
    "REPRICE_INTERVAL_NOT_ELAPSED", "CHASE_LIMIT_EXCEEDED",
    "OTHER_BOUNDED_REASON",
})
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
    "consumer_create_requested", "consumer_started", "consumer_stopped",
    "first_reference_ready", "first_qualification_ready", "all_references_terminal",
)

_STARTUP_COUNTERS = (
    "reference_warmup_symbols_total", "reference_warmup_symbols_completed",
    "reference_warmup_symbols_accepted", "reference_warmup_symbols_rejected",
    "reference_warmup_symbols_ready", "reference_warmup_symbols_pending",
    "reference_warmup_symbols_failed",
    "raw_callbacks_received", "callbacks_enqueued", "callbacks_dequeued",
    "decode_attempts", "decode_successes", "decode_failures", "decode_ignored",
    "normalized_market_events_emitted", "scanner_market_events_received",
    "scanner_evaluations",
    "subscription_requested_symbols",
    "subscription_completed_symbols",
    "subscription_batch_count",
    "unique_symbols_observed_before_feed_healthy",
    "observation_channel_count", "ready_symbol_count",
    "pending_reference_symbol_count", "retained_channel_count",
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
        self._reconciliation_counters: dict[str, int] = {
            "eligibility_checks": 0,
            "dirty_triggers": 0,
            "quantity_change_triggers": 0,
            "periodic_audit_triggers": 0,
            "executions": 0,
            "successes": 0,
            "failures": 0,
            "noops": 0,
            "retries": 0,
        }
        self._order_flow_samples: dict[str, deque[float]] = {
            "FOOTPRINT": deque(maxlen=128),
            "CAPITAL_FLOW": deque(maxlen=128),
        }
        self._order_flow_metrics: dict[str, dict[str, object]] = {
            endpoint: {
                "attempts": 0,
                "successes": 0,
                "failures": 0,
                "consecutive_failures": 0,
                "latest_failure": None,
            }
            for endpoint in self._order_flow_samples
        }
        self._reference_samples: deque[float] = deque(maxlen=128)
        self._reference_metrics: dict[str, object] = {
            "requests": 0,
            "successes": 0,
            "failures": 0,
            "retries": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "concurrency_high_water": 1,
            "latest_failure": None,
        }
        self._stream_failure_samples: deque[dict[str, object]] = deque(
            maxlen=_MAX_STREAM_FAILURE_SAMPLES
        )
        self._stream_metrics: dict[str, object] = {
            "receive_failures_total": 0,
            "reconnect_attempts_total": 0,
            "reconnect_successes": 0,
            "reconnect_failures": 0,
            "reconnect_exhausted": 0,
            "consecutive_receive_failures": 0,
            "current_stream_generation": None,
            "stale_generation_callbacks_rejected": 0,
            "terminal_stream_failures": 0,
            "latest_lifecycle_state": "DISCONNECTED",
            "last_good_raw_callback_at": None,
            "last_normalized_event_at": None,
            "first_receive_failure_at": None,
            "reconnect_started_at": None,
            "reconnect_completed_at": None,
            "terminal_failure_at": None,
            "callback_ingestion_halted_at": None,
            "consumer_stopped_at": None,
            "transport_disconnected_at": None,
            "broker_disconnect_started_at": None,
            "broker_disconnect_completed_at": None,
            "reconnect_result": None,
            "latest_failure": None,
            "failure_samples": (),
        }
        self._scanner_population: dict[str, object] = {
            "active_symbols": 0,
            "adapter_state_count": 0,
            "complete_decision_count": 0,
            "qualified_count": 0,
            "watching_count": 0,
            "near_miss_count": 0,
            "hidden_rejected_count": 0,
            "missing_field_counts": {},
            "completeness_transitions": {},
            "rejection_counts": {},
            "failed_rule_distribution": {"0": 0, "1": 0, "2": 0, "3_plus": 0},
            "all_decision_rank_count": 0,
            "displayed_candidate_count": 0,
            "highest_hidden_rejected_score": None,
            "hidden_ranked_ahead_by_displayed_candidate": {},
            "top_sample": (),
        }
        self._entry_conversion_counters = {name: 0 for name in _ENTRY_COUNTERS}
        self._entry_refusal_counts = {name: 0 for name in sorted(_PURSUIT_REASONS)}
        self._entry_lifecycle_records: OrderedDict[str, dict[str, object]] = OrderedDict()
        self._entry_pursuit_records: deque[dict[str, object]] = deque(maxlen=_MAX_ENTRY_PURSUIT_RECORDS)
        self._entry_setup_records: deque[dict[str, object]] = deque(maxlen=_MAX_SETUP_TRANSITION_RECORDS)
        self._protection_events: deque[dict[str, object]] = deque(maxlen=_MAX_PROTECTION_EVENTS)
        self._strategy_selection_records: deque[dict[str, object]] = deque(maxlen=_MAX_STRATEGY_SELECTION_RECORDS)
        self._entry_last_setup_state: OrderedDict[str, str] = OrderedDict()
        self._run_id = uuid4().hex
        self._process_started_at = datetime.now(UTC)
        self._artifact_path: Path | None = None
        self._application_version = os.getenv(
            "ATLAS_APPLICATION_VERSION", "unknown"
        )
        self._branch = os.getenv("ATLAS_BRANCH") or os.getenv(
            "GIT_BRANCH", "unknown"
        )
        self._durable_flush_seconds = _bounded_flush_seconds(
            os.getenv("ATLAS_PERFORMANCE_FLUSH_SECONDS")
        )
        self._durable_stop = None
        self._durable_wakeup = None
        self._durable_thread = None
        self._durable_write_failures = 0
        self._durable_checkpoint_count = 0

    @property
    def run_id(self) -> str:
        """Return the current bounded process/runtime diagnostic identity."""
        with self._lock:
            return self._run_id

    @property
    def artifact_path(self) -> Path | None:
        with self._lock:
            return self._artifact_path

    def start_run(
        self,
        *,
        artifact_path: str | Path | None = None,
        branch: str | None = None,
        application_version: str | None = None,
        flush_seconds: float | None = None,
    ) -> str:
        """Start one durable run without changing performance semantics.

        This is a lifecycle operation, never called from a market-event hot
        path.  The writer thread owns all filesystem I/O; callers only update
        bounded in-memory state and signal it.
        """
        self.finish_run()
        # The singleton is reused by the desktop composition.  Reset all
        # bounded counters so each artifact describes exactly one run.
        self.__init__()
        with self._lock:
            self._run_id = uuid4().hex
            self._process_started_at = datetime.now(UTC)
            self._artifact_path = Path(
                artifact_path
                if artifact_path is not None
                else os.getenv(
                    "ATLAS_PERFORMANCE_ARTIFACT_PATH",
                    str(Path(os.getenv("TEMP", ".")) / "atlas-performance" / f"{self._run_id}.json"),
                )
            )
            self._branch = str(branch or os.getenv("ATLAS_BRANCH") or os.getenv("GIT_BRANCH", "unknown"))
            self._application_version = str(
                application_version
                or os.getenv("ATLAS_APPLICATION_VERSION", "unknown")
            )
            if flush_seconds is not None:
                self._durable_flush_seconds = _bounded_flush_seconds(flush_seconds)
            self._durable_write_failures = 0
            self._durable_checkpoint_count = 0
            stop_event = Event()
            wakeup = Event()
            self._durable_stop = stop_event
            self._durable_wakeup = wakeup
            thread = Thread(
                target=self._durable_writer_loop,
                args=(stop_event, wakeup),
                name="atlas-performance-diagnostics-writer",
                daemon=True,
            )
            self._durable_thread = thread
        thread.start()
        self.request_checkpoint()
        return self.run_id

    def request_checkpoint(self) -> bool:
        """Request an asynchronous aggregate snapshot; never performs I/O."""
        with self._lock:
            wakeup = self._durable_wakeup
            thread = self._durable_thread
        if wakeup is None or thread is None or not thread.is_alive():
            return False
        wakeup.set()
        return True

    def finish_run(self) -> None:
        """Stop the writer and atomically publish one final run snapshot."""
        with self._lock:
            stop_event = self._durable_stop
            wakeup = self._durable_wakeup
            thread = self._durable_thread
        if stop_event is None:
            return
        stop_event.set()
        if wakeup is not None:
            wakeup.set()
        if thread is not None:
            thread.join(2.0)
        self._write_durable_artifact("FINAL")
        with self._lock:
            self._durable_stop = None
            self._durable_wakeup = None
            self._durable_thread = None

    def durable_metrics(self) -> dict[str, object]:
        with self._lock:
            return {
                "run_id": self._run_id,
                "artifact_path": None if self._artifact_path is None else str(self._artifact_path),
                "checkpoint_count": self._durable_checkpoint_count,
                "write_failures": self._durable_write_failures,
            }

    def _durable_writer_loop(self, stop_event, wakeup) -> None:
        while not stop_event.is_set():
            wakeup.wait(self._durable_flush_seconds)
            wakeup.clear()
            if stop_event.is_set():
                return
            self._write_durable_artifact("CHECKPOINT")

    def _write_durable_artifact(self, status: str) -> None:
        with self._lock:
            destination = self._artifact_path
            run_id = self._run_id
            process_started_at = self._process_started_at
            branch = self._branch
            application_version = self._application_version
            checkpoint_count = self._durable_checkpoint_count
        if destination is None:
            return
        try:
            captured_at = datetime.now(UTC)
            startup = self.startup_metrics()
            snapshot = self.snapshot()
            payload = {
                "schema_version": _DURABLE_SCHEMA_VERSION,
                "run_id": run_id,
                "status": status,
                "process_started_at": process_started_at.isoformat(),
                "runtime_started_at": startup.get("runtime_started_at"),
                "shutdown_at": captured_at.isoformat() if status == "FINAL" else None,
                "captured_at": captured_at.isoformat(),
                "branch": branch,
                "application_version": application_version,
                "metrics": _json_safe(asdict(snapshot)),
                "startup": _json_safe(startup),
                "forensic": _json_safe(self.forensic_metrics()),
                "reconciliation": _json_safe(self.reconciliation_metrics()),
                "order_flow": _json_safe(self.order_flow_metrics()),
                "reference": _json_safe(self.reference_metrics()),
                "scanner_population": _json_safe(self.scanner_population_metrics()),
                "entry_conversion": _json_safe(self.entry_conversion_metrics()),
                "strategy_selection": _json_safe(self.strategy_selection_metrics()),
                "stream": _json_safe(self.stream_metrics()),
                "durable": {
                    "checkpoint_count": checkpoint_count + 1,
                    "write_failures": self.durable_metrics()["write_failures"],
                },
            }
            raw = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.{run_id}.tmp")
            temporary.write_text(raw, encoding="utf-8")
            os.replace(temporary, destination)
            with self._lock:
                self._durable_checkpoint_count += 1
        except Exception:
            with self._lock:
                self._durable_write_failures += 1

    def increment(self, name: str, amount: int = 1) -> None:
        if name not in self._counters:
            raise KeyError(name)
        if amount < 0:
            raise ValueError("performance counter increment cannot be negative")
        with self._lock:
            self._counters[name] += amount

    def record_entry_counter(self, name: str, amount: int = 1) -> None:
        """Record bounded entry-lifecycle counters without performing I/O."""
        if name not in _ENTRY_COUNTERS or amount < 0:
            return
        try:
            with self._lock:
                self._entry_conversion_counters[name] += amount
        except Exception:
            return

    def record_entry_lifecycle(self, lifecycle_id: str, **values: object) -> None:
        """Merge one bounded lifecycle record; diagnostics never become authority."""
        try:
            identity = str(lifecycle_id).strip()
            if not identity:
                return
            with self._lock:
                record = self._entry_lifecycle_records.pop(identity, {})
                record["lifecycle_id"] = identity
                for key, value in values.items():
                    if value is not None:
                        record[str(key)] = _json_safe(value)
                self._entry_lifecycle_records[identity] = record
                while len(self._entry_lifecycle_records) > _MAX_ENTRY_LIFECYCLE_RECORDS:
                    self._entry_lifecycle_records.popitem(last=False)
        except Exception:
            return

    def record_pursuit_evaluation(self, *, reason: str, **values: object) -> None:
        """Append a bounded, payload-free structured pursuit decision."""
        try:
            normalized = str(reason).strip().upper() or "OTHER_BOUNDED_REASON"
            if normalized not in _PURSUIT_REASONS:
                normalized = "OTHER_BOUNDED_REASON"
            record = {"evaluation_result": normalized}
            record.update({str(key): _json_safe(value) for key, value in values.items() if value is not None})
            with self._lock:
                self._entry_conversion_counters["pursuit_evaluations"] += 1
                if normalized != "REPLACE_APPROVED":
                    self._entry_refusal_counts[normalized] += 1
                self._entry_pursuit_records.append(record)
        except Exception:
            return

    def record_setup_transition(self, *, symbol: str, state: str, **values: object) -> None:
        """Record only state changes, bounded per symbol, for setup reconstruction."""
        try:
            normalized_symbol = str(symbol).strip().upper()
            normalized_state = str(state).strip().upper() or "OTHER"
            if not normalized_symbol:
                return
            with self._lock:
                if self._entry_last_setup_state.get(normalized_symbol) == normalized_state:
                    return
                self._entry_last_setup_state.pop(normalized_symbol, None)
                self._entry_last_setup_state[normalized_symbol] = normalized_state
                record = {"symbol": normalized_symbol, "state": normalized_state}
                record.update({str(key): _json_safe(value) for key, value in values.items() if value is not None})
                self._entry_setup_records.append(record)
                while len(self._entry_last_setup_state) > _MAX_SETUP_TRANSITION_RECORDS:
                    self._entry_last_setup_state.popitem(last=False)
        except Exception:
            return

    def record_partial_fill(self, *, lifecycle_id: str, **values: object) -> None:
        self.record_entry_counter("partial_fill_events")
        self.record_entry_lifecycle(lifecycle_id, **values)

    def record_strategy_selection(self, **values: object) -> None:
        """Record bounded adapter selection metadata; never performs I/O."""
        try:
            allowed = {
                "strategies_evaluated", "strategies_matched", "strategy_memberships",
                "selected_execution_strategy", "suppressed_duplicate_strategies",
                "opportunity_anchor", "execution_identity", "selection_score",
                "selection_priority", "adapter_rejection_reason",
                "strategy_evaluations",
                "symbol", "composition_outcome", "taxonomy_selected_strategy",
                "legacy_setup", "arbitration_winner", "suppressed_candidate",
            }
            record = {str(key): _json_safe(value) for key, value in values.items() if key in allowed}
            with self._lock:
                self._strategy_selection_records.append(record)
        except Exception:
            return

    def strategy_selection_metrics(self) -> dict[str, object]:
        with self._lock:
            return {
                "records": tuple(self._strategy_selection_records),
                "bounds": {"records": _MAX_STRATEGY_SELECTION_RECORDS},
            }

    def entry_conversion_metrics(self) -> dict[str, object]:
        with self._lock:
            return {
                "counters": dict(self._entry_conversion_counters),
                "replacement_refusal_counts_by_reason": dict(self._entry_refusal_counts),
                "lifecycle_records": tuple(self._entry_lifecycle_records.values()),
                "pursuit_evaluations": tuple(self._entry_pursuit_records),
                "setup_transitions": tuple(self._entry_setup_records),
                "bounds": {
                    "lifecycle_records": _MAX_ENTRY_LIFECYCLE_RECORDS,
                    "pursuit_evaluations": _MAX_ENTRY_PURSUIT_RECORDS,
                    "setup_transitions": _MAX_SETUP_TRANSITION_RECORDS,
                },
            }

    def increment_reconciliation_counter(self, name: str, amount: int = 1) -> None:
        """Increment a fixed, bounded protection-reconciliation counter."""
        if name not in self._reconciliation_counters:
            raise KeyError(name)
        if amount < 0:
            raise ValueError("performance counter increment cannot be negative")
        with self._lock:
            self._reconciliation_counters[name] += amount

    def record_protection_event(self, *, state: str, **values: object) -> None:
        """Record one bounded protection handoff event in memory.

        The normal asynchronous diagnostics writer persists this with the next
        artifact flush.  Protection diagnostics must never become an execution
        dependency, so failures are intentionally isolated here.
        """
        try:
            record = {"state": str(state).strip().upper()}
            record.update({str(key): _json_safe(value) for key, value in values.items() if value is not None})
            with self._lock:
                self._protection_events.append(record)
        except Exception:
            return

    def reconciliation_metrics(self) -> dict[str, object]:
        with self._lock:
            result: dict[str, object] = dict(self._reconciliation_counters)
            result["protection_events"] = tuple(self._protection_events)
            result["protection_event_limit"] = _MAX_PROTECTION_EVENTS
            return result

    def record_order_flow_result(
        self,
        endpoint: str,
        duration_ms: float,
        *,
        success: bool,
        failure: str | None = None,
    ) -> None:
        key = str(endpoint).upper()
        if key not in self._order_flow_samples:
            raise KeyError(key)
        if duration_ms < 0:
            raise ValueError("order-flow timing cannot be negative")
        with self._lock:
            samples = self._order_flow_samples[key]
            samples.append(duration_ms)
            metrics = self._order_flow_metrics[key]
            metrics["attempts"] = int(metrics["attempts"]) + 1
            if success:
                metrics["successes"] = int(metrics["successes"]) + 1
                metrics["consecutive_failures"] = 0
            else:
                metrics["failures"] = int(metrics["failures"]) + 1
                metrics["consecutive_failures"] = int(metrics["consecutive_failures"]) + 1
                metrics["latest_failure"] = failure

    def order_flow_metrics(self) -> dict[str, dict[str, object]]:
        with self._lock:
            result: dict[str, dict[str, object]] = {}
            for endpoint, samples in self._order_flow_samples.items():
                ordered = sorted(samples)
                values = dict(self._order_flow_metrics[endpoint])
                values.update(
                    latency_p50_ms=round(_percentile(ordered, 0.50), 3),
                    latency_p90_ms=round(_percentile(ordered, 0.90), 3),
                    latency_p99_ms=round(_percentile(ordered, 0.99), 3),
                    latency_max_ms=round(max(ordered, default=0.0), 3),
                )
                result[endpoint] = values
            return result

    def record_reference_result(
        self,
        duration_ms: float,
        *,
        success: bool,
        failure: str | None = None,
        cache_hit: bool | None = None,
    ) -> None:
        if duration_ms < 0:
            raise ValueError("reference timing cannot be negative")
        with self._lock:
            self._reference_samples.append(duration_ms)
            metrics = self._reference_metrics
            metrics["requests"] = int(metrics["requests"]) + 1
            if success:
                metrics["successes"] = int(metrics["successes"]) + 1
            else:
                metrics["failures"] = int(metrics["failures"]) + 1
                metrics["latest_failure"] = failure
            if cache_hit is True:
                metrics["cache_hits"] = int(metrics["cache_hits"]) + 1
            elif cache_hit is False:
                metrics["cache_misses"] = int(metrics["cache_misses"]) + 1

    def reference_metrics(self) -> dict[str, object]:
        with self._lock:
            ordered = sorted(self._reference_samples)
            values = dict(self._reference_metrics)
            values.update(
                latency_p50_ms=round(_percentile(ordered, 0.50), 3),
                latency_p90_ms=round(_percentile(ordered, 0.90), 3),
                latency_p99_ms=round(_percentile(ordered, 0.99), 3),
                latency_max_ms=round(max(ordered, default=0.0), 3),
            )
            return values

    @staticmethod
    def _sanitize_exception_message(error: Exception | None) -> str | None:
        if error is None:
            return None
        message = str(error).replace("\r", " ").replace("\n", " ")
        message = re.sub(
            r"(?i)(token|access_token|refresh_token|password|secret|cookie|authorization)"
            r"\s*[:=]\s*[^,; ]+",
            r"\1=[REDACTED]",
            message,
        )
        message = re.sub(r"(?i)bearer\s+\S+", "Bearer [REDACTED]", message)
        return message[:256]

    def record_stream_lifecycle(
        self,
        lifecycle: str,
        *,
        error: Exception | None = None,
        attempt: int = 0,
        maximum_attempts: int | None = None,
        generation: int | None = None,
        session_id_hash: str | None = None,
        reconnect_result: str | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        """Record bounded stream failure/reconnect lifecycle evidence."""
        event = str(lifecycle).strip().lower()
        now = (timestamp or datetime.now(UTC)).isoformat()
        with self._lock:
            metrics = self._stream_metrics
            if generation is not None:
                metrics["current_stream_generation"] = int(generation)
            if event in {"reconnecting", "receive_failed"}:
                metrics["receive_failures_total"] = int(
                    metrics["receive_failures_total"]
                ) + 1
                metrics["reconnect_attempts_total"] = int(
                    metrics["reconnect_attempts_total"]
                ) + (1 if event == "reconnecting" else 0)
                metrics["consecutive_receive_failures"] = int(
                    metrics["consecutive_receive_failures"]
                ) + 1
                metrics["latest_lifecycle_state"] = (
                    "RECONNECTING" if event == "reconnecting" else "RECEIVE_FAILED"
                )
                if metrics["first_receive_failure_at"] is None:
                    metrics["first_receive_failure_at"] = now
                if event == "reconnecting":
                    metrics["reconnect_started_at"] = now
            elif event == "reconnected":
                metrics["reconnect_successes"] = int(
                    metrics["reconnect_successes"]
                ) + 1
                metrics["consecutive_receive_failures"] = 0
                metrics["latest_lifecycle_state"] = "RECONNECTED"
                metrics["reconnect_completed_at"] = now
                metrics["reconnect_result"] = reconnect_result or "SUCCESS"
            elif event in {"reconnect_failed", "reconnect_failure"}:
                metrics["reconnect_failures"] = int(
                    metrics["reconnect_failures"]
                ) + 1
                metrics["latest_lifecycle_state"] = "RECONNECTING"
                metrics["reconnect_result"] = reconnect_result or "FAILED"
            elif event == "terminal_failure":
                if metrics["first_receive_failure_at"] is None:
                    metrics["receive_failures_total"] = int(
                        metrics["receive_failures_total"]
                    ) + 1
                    metrics["consecutive_receive_failures"] = int(
                        metrics["consecutive_receive_failures"]
                    ) + 1
                metrics["terminal_stream_failures"] = int(
                    metrics["terminal_stream_failures"]
                ) + 1
                metrics["reconnect_exhausted"] = int(
                    metrics["reconnect_exhausted"]
                ) + 1
                metrics["latest_lifecycle_state"] = "TERMINAL_FAILED"
                metrics["terminal_failure_at"] = now
                metrics["reconnect_result"] = reconnect_result or "EXHAUSTED"
            elif event in {"connected", "transport_connected"}:
                metrics["latest_lifecycle_state"] = "CONNECTED"
                metrics["consecutive_receive_failures"] = 0
            elif event == "disconnected":
                metrics["latest_lifecycle_state"] = "DISCONNECTED"
                metrics["transport_disconnected_at"] = now

            sanitized = self._sanitize_exception_message(error)
            if sanitized is not None:
                failure = {
                    "timestamp": now,
                    "exception_class": type(error).__name__,
                    "message": sanitized,
                    "failure_stage": event,
                    "generation": generation,
                    "session_id_hash": session_id_hash,
                    "reconnect_attempt": max(0, int(attempt)),
                    "maximum_attempts": (
                        None if maximum_attempts is None else max(0, int(maximum_attempts))
                    ),
                    "terminal": event == "terminal_failure",
                }
                self._stream_failure_samples.append(failure)
                metrics["latest_failure"] = failure
                metrics["failure_samples"] = tuple(
                    dict(item) for item in self._stream_failure_samples
                )

    def record_stream_raw_callback(
        self,
        *,
        timestamp: datetime | None = None,
        generation: int | None = None,
    ) -> None:
        with self._lock:
            if generation is not None:
                self._stream_metrics["current_stream_generation"] = int(generation)
            self._stream_metrics["last_good_raw_callback_at"] = (
                timestamp or datetime.now(UTC)
            ).isoformat()
            self._stream_metrics["latest_lifecycle_state"] = "CONNECTED"

    def record_stream_normalized_event(self, timestamp: datetime | None = None) -> None:
        with self._lock:
            self._stream_metrics["last_normalized_event_at"] = (
                timestamp or datetime.now(UTC)
            ).isoformat()

    def record_stream_stale_generation_rejection(self) -> None:
        with self._lock:
            self._stream_metrics["stale_generation_callbacks_rejected"] = int(
                self._stream_metrics["stale_generation_callbacks_rejected"]
            ) + 1

    def record_stream_boundary(
        self,
        boundary: str,
        *,
        timestamp: datetime | None = None,
    ) -> None:
        field_by_boundary = {
            "callback_ingestion_halted": "callback_ingestion_halted_at",
            "consumer_stopped": "consumer_stopped_at",
            "transport_disconnected": "transport_disconnected_at",
            "broker_disconnect_started": "broker_disconnect_started_at",
            "broker_disconnect_completed": "broker_disconnect_completed_at",
        }
        field = field_by_boundary.get(str(boundary))
        if field is None:
            raise ValueError(f"unknown stream boundary: {boundary}")
        with self._lock:
            self._stream_metrics[field] = (
                timestamp or datetime.now(UTC)
            ).isoformat()
            if boundary == "consumer_stopped":
                self._stream_metrics["latest_lifecycle_state"] = "DISCONNECTED"

    def stream_metrics(self) -> dict[str, object]:
        with self._lock:
            return {
                **self._stream_metrics,
                "failure_samples": tuple(
                    dict(item) for item in self._stream_failure_samples
                ),
            }

    def record_scanner_population_base(
        self,
        *,
        active_symbols: int,
        adapter_state_count: int,
        missing_field_counts: object,
        completeness_transitions: object = None,
    ) -> None:
        """Record current adapter population without retaining symbol history."""
        with self._lock:
            self._scanner_population["active_symbols"] = max(0, int(active_symbols))
            self._scanner_population["adapter_state_count"] = max(0, int(adapter_state_count))
            self._scanner_population["missing_field_counts"] = {
                str(key): max(0, int(value))
                for key, value in dict(missing_field_counts).items()
            }
            self._scanner_population["completeness_transitions"] = {
                str(key): dict(value)
                for key, value in dict(completeness_transitions or {}).items()
            }

    def record_scanner_population_display(self, values: dict[str, object]) -> None:
        """Replace bounded decision/display aggregates for the latest snapshot."""
        with self._lock:
            for key, value in values.items():
                if key == "top_sample":
                    self._scanner_population[key] = tuple(value)[:25]
                else:
                    self._scanner_population[key] = value

    def scanner_population_metrics(self) -> dict[str, object]:
        with self._lock:
            return _json_safe(dict(self._scanner_population))

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

    def set_startup_counter(self, name: str, value: int) -> None:
        """Set a bounded startup gauge such as pending reference work."""
        if name not in _STARTUP_COUNTERS:
            raise ValueError(f"unknown startup diagnostic counter: {name}")
        if value < 0:
            raise ValueError("startup diagnostic counter cannot be negative")
        with self._lock:
            self._startup_counters[name] = value

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
                    None
                    if (
                        started is None
                        or finished is None
                        or finished < started
                    )
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


def _bounded_flush_seconds(value: object) -> float:
    try:
        candidate = _DEFAULT_FLUSH_SECONDS if value is None else float(value)
    except (TypeError, ValueError):
        candidate = _DEFAULT_FLUSH_SECONDS
    if candidate != candidate or candidate in (float("inf"), float("-inf")):
        candidate = _DEFAULT_FLUSH_SECONDS
    return min(300.0, max(0.05, candidate))


def _json_safe(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


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
