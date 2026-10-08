"""Bounded, payload-free performance counters for the Atlas runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from collections import OrderedDict, deque
from datetime import UTC, datetime
from contextlib import contextmanager
from enum import StrEnum
import json
import os
import re
from hashlib import sha256
from pathlib import Path
from threading import Event, Thread, local, RLock, current_thread
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
_MAX_MANAGEMENT_EVENTS = 512
_MAX_STRATEGY_SELECTION_RECORDS = 256
_MAX_ENTRY_FUNNEL_RECORDS = 512
_MAX_PRICE_GATE_SAMPLES = 512
_MAX_PRICE_GATE_QUANTILE_SAMPLES = 2048
_MAX_PRICE_GATE_SETUP_BREAKDOWNS = 16
_MAX_STREAM_OBSERVABILITY_EVENTS = 512
_MAX_RADAR_OBSERVABILITY_EVENTS = 128
_MAX_SHUTDOWN_EVENTS = 32
_CRITICAL_STREAM_EVENTS = frozenset({
    "PAYLOAD_STALE_CONTEXT", "RECOVERY_STARTED", "RECOVERY_SUCCEEDED",
    "RECOVERY_FAILED", "RECOVERY_COALESCED", "CONNECT_REQUESTED",
    "CONNECT_STARTED", "CONNECT_SUCCEEDED", "DISCONNECT_REQUESTED",
    "GENERATION_RETIRED", "RECEIVE_LOOP_STARTED", "SUBSCRIBE_REQUESTED",
    "SUBSCRIBE_COMPLETED", "SUBSCRIBE_FAILED", "UNSUBSCRIBE_REQUESTED",
    "UNSUBSCRIBE_COMPLETED", "CALLBACK_REJECTED",
})
_ENTRY_FUNNEL_STAGES = (
    "SCANNER_QUALIFIED", "SETUP_FORMING", "SETUP_TRIGGERED",
    "TECHNICAL_SIGNAL", "TECHNICAL_SIGNAL_CLEARED", "FRESHNESS_CHECK",
    "PROCESSING_AGE_CHECK", "EXECUTION_QUOTE_REQUESTED",
    "EXECUTION_QUOTE_RETURNED", "EXECUTION_QUOTE_REJECTED",
    "EXECUTION_QUOTE_ACCEPTED", "EXECUTION_PERMISSION",
    "HALT_SESSION_GATE", "ACCOUNT_GATE", "EXECUTION_QUALITY",
    "ADAPTIVE_PRICE_GATE", "RETAINED_SOURCE_AGE",
    "RISK_AUTHORIZATION", "REWARD_GATE", "PAPER_AUTHORIZATION",
    "ORDER_INTENT", "ORDER_SUBMITTED", "ORDER_ACKNOWLEDGED",
    "ORDER_REJECTED", "FILL", "PARTIAL_FILL",
    "OPPORTUNITY_ARMED", "OPPORTUNITY_WAITING_EXECUTION",
    "OPPORTUNITY_EXECUTABLE", "OPPORTUNITY_AUTHORIZED",
    "OPPORTUNITY_INVALIDATED", "OPPORTUNITY_EXPIRED",
    "OPPORTUNITY_REJECTED_HARD_SAFETY",
)
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
    "INSUFFICIENT_REMAINING_REWARD",
    "OTHER_BOUNDED_REASON",
})


class ShutdownOrigin(StrEnum):
    OPERATOR_STOP = "OPERATOR_STOP"
    GUI_CLOSE = "GUI_CLOSE"
    APPLICATION_QUIT = "APPLICATION_QUIT"
    EXTERNAL_STOP_REQUEST = "EXTERNAL_STOP_REQUEST"
    CONSUMER_FAILURE = "CONSUMER_FAILURE"
    BROKER_FAILURE = "BROKER_FAILURE"
    MARKET_DATA_FAILURE = "MARKET_DATA_FAILURE"
    OWNERSHIP_FAILURE = "OWNERSHIP_FAILURE"
    BACKGROUND_TASK_FAILURE = "BACKGROUND_TASK_FAILURE"
    PROCESS_SIGNAL = "PROCESS_SIGNAL"
    UNKNOWN = "UNKNOWN"


class ShutdownReason(StrEnum):
    OPERATOR_REQUESTED = "OPERATOR_REQUESTED"
    GUI_WINDOW_CLOSED = "GUI_WINDOW_CLOSED"
    APPLICATION_EXIT = "APPLICATION_EXIT"
    EXTERNAL_REQUESTED = "EXTERNAL_REQUESTED"
    CONSUMER_TERMINATED = "CONSUMER_TERMINATED"
    BROKER_EXCEPTION = "BROKER_EXCEPTION"
    MARKET_DATA_TERMINAL_FAILURE = "MARKET_DATA_TERMINAL_FAILURE"
    OWNERSHIP_REJECTED = "OWNERSHIP_REJECTED"
    BACKGROUND_TASK_TERMINATED = "BACKGROUND_TASK_TERMINATED"
    PROCESS_SIGNAL_RECEIVED = "PROCESS_SIGNAL_RECEIVED"
    UNSPECIFIED = "UNSPECIFIED"
_EXECUTION_GATE_COUNTERS = tuple(sorted({
    "QUOTE_REJECTED_MISSING", "QUOTE_REJECTED_SYMBOL_MISMATCH",
    "QUOTE_REJECTED_BID_STALE", "QUOTE_REJECTED_LAST_TRADE_STALE",
    "QUOTE_REJECTED_TECHNICAL_MINUTE", "QUOTE_REJECTED_INVALID",
    "ENTRY_PRICE_DISPLACED_PERCENT", "ENTRY_PRICE_DISPLACED_ABSOLUTE",
    "ENTRY_PRICE_DISPLACED_BOTH", "ENTRY_PRICE_INVALID", "ENTRY_PRICE_BELOW_STOP",
    "RISK_REJECTED_INVALID_INPUT", "RISK_REJECTED_STOP_DISTANCE",
    "RISK_REJECTED_ZERO_SHARES", "RISK_REJECTED_ENGINE", "RISK_REJECTED_CAMPAIGN_LOSS", "RISK_REJECTED_EXPOSURE",
    "RISK_REJECTED_SYMBOL_AUTHORIZATION", "RISK_REJECTED_BROKER_RESTRICTION",
    "CLEAR_PRICE_DISPLACEMENT", "CLEAR_RISK", "CLEAR_QUOTE_FRESHNESS",
    "CLEAR_TREATMENT_PENDING", "CLEAR_REWARD", "CLEAR_ACCOUNT", "CLEAR_SESSION",
    "CLEAR_LIFECYCLE",
}))
_QUOTE_AGE_FIELDS = ("bid_age", "last_trade_age", "processing_age", "delivery_age")
_PRICE_GATE_RESULT_CODES = (
    "ACCEPTED", "ENTRY_PRICE_DISPLACED_PERCENT", "ENTRY_PRICE_DISPLACED_ABSOLUTE",
    "ENTRY_PRICE_DISPLACED_BOTH", "ENTRY_PRICE_INVALID", "ENTRY_PRICE_BELOW_STOP",
)
_PRICE_GATE_DISTRIBUTION_FIELDS = (
    "actual_displacement_percent", "risk_normalized_extension", "trigger_age_seconds",
    "entry_ready_age_seconds", "signal_age_seconds", "remaining_first_target_r",
    "remaining_final_target_r",
)
_PRICE_GATE_CONTINUATION_CLASSES = (
    "INITIAL_BREAKOUT", "CONTINUATION", "RECLAIM", "REACCELERATION", "OTHER",
)
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
    research_events_coalesced: int = 0
    research_failures: int = 0
    processing_delayed_events: int = 0
    report_refresh_failures: int = 0
    latency_diagnostics_persisted: int = 0
    callback_threshold_events: int = 0
    runtime_projection_events_rejected: int = 0
    quick_scalper_stream_observations: int = 0
    quick_scalper_assessments: int = 0
    quick_scalper_opportunities: int = 0
    quick_scalper_executable: int = 0
    quick_scalper_authorization_attempts: int = 0
    quick_scalper_rejections: int = 0
    quick_scalper_orders_submitted: int = 0
    quick_scalper_execution_quote_unavailable: int = 0
    quick_scalper_stale_preview_refresh_attempts: int = 0
    quick_scalper_stale_preview_refresh_available: int = 0
    quick_scalper_stale_preview_refresh_advanced: int = 0
    quick_scalper_reconfirm_not_executable: int = 0
    quick_scalper_account_unavailable: int = 0
    quick_scalper_risk_rejected: int = 0
    quick_scalper_insufficient_executable_size: int = 0
    quick_scalper_bridge_reached: int = 0
    quick_scalper_bridge_rejected: int = 0
    quick_scalper_bridge_authorized: int = 0
    # Exact bounded reason attribution for post-trigger reconfirmation.
    quick_scalper_reconfirm_insufficient_edge: int = 0
    quick_scalper_reconfirm_provider_data_stale: int = 0
    quick_scalper_reconfirm_invalid_execution_quote: int = 0
    quick_scalper_reconfirm_session_not_allowed: int = 0
    quick_scalper_reconfirm_price_not_eligible: int = 0
    quick_scalper_reconfirm_risk_not_authorized: int = 0
    quick_scalper_reconfirm_other: int = 0
    # Exact bounded attribution for the initial streaming policy assessment.
    quick_scalper_initial_insufficient_edge: int = 0
    quick_scalper_initial_provider_data_stale: int = 0
    quick_scalper_initial_invalid_execution_quote: int = 0
    quick_scalper_initial_session_not_allowed: int = 0
    quick_scalper_initial_price_not_eligible: int = 0
    quick_scalper_initial_risk_not_authorized: int = 0
    quick_scalper_initial_other: int = 0
    # Exact bounded reason attribution from canonical risk sizing.
    quick_scalper_risk_invalid_input: int = 0
    quick_scalper_risk_stop_distance: int = 0
    quick_scalper_risk_symbol_authorization: int = 0
    quick_scalper_risk_broker_restriction: int = 0
    quick_scalper_risk_engine: int = 0
    quick_scalper_risk_campaign_loss: int = 0
    quick_scalper_risk_zero_shares: int = 0
    quick_scalper_risk_exposure: int = 0
    quick_scalper_risk_other: int = 0
    # Exact bounded attribution for authoritative confirmation freshness.
    quick_scalper_freshness_last_stale: int = 0
    quick_scalper_freshness_bid_stale: int = 0
    quick_scalper_freshness_ask_stale: int = 0
    quick_scalper_freshness_last_future: int = 0
    quick_scalper_freshness_bid_future: int = 0
    quick_scalper_freshness_ask_future: int = 0
    quick_scalper_freshness_last_only: int = 0
    quick_scalper_freshness_bid_only: int = 0
    quick_scalper_freshness_ask_only: int = 0
    quick_scalper_freshness_multiple: int = 0
    quick_scalper_freshness_last_age_ms_total: int = 0
    quick_scalper_freshness_bid_age_ms_total: int = 0
    quick_scalper_freshness_ask_age_ms_total: int = 0
    quick_scalper_freshness_last_age_ms_max: int = 0
    quick_scalper_freshness_bid_age_ms_max: int = 0
    quick_scalper_freshness_ask_age_ms_max: int = 0
    warrior_full_evaluations: int = 0
    fast_mover_refresh_eligible: int = 0
    fast_mover_refresh_due: int = 0
    fast_mover_refresh_executed: int = 0
    acceleration_point_appended: int = 0
    acceleration_point_duplicate_skipped: int = 0
    acceleration_insufficient_points: int = 0
    acceleration_lifetime_exceeded: int = 0
    acceleration_predicate_failed: int = 0
    acceleration_forming: int = 0
    acceleration_triggered: int = 0
    acceleration_masked_by_stronger_setup: int = 0
    reacceleration_insufficient_points: int = 0
    reacceleration_lifetime_exceeded: int = 0
    reacceleration_predicate_failed: int = 0
    reacceleration_forming: int = 0
    reacceleration_triggered: int = 0
    reacceleration_masked_by_stronger_setup: int = 0
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
    trade_intelligence_queue_discovery_depth: int = 0
    trade_intelligence_queue_discovery_oldest_ms: int = 0
    trade_intelligence_queue_bar_depth: int = 0
    trade_intelligence_queue_bar_oldest_ms: int = 0
    trade_intelligence_queue_decision_depth: int = 0
    trade_intelligence_queue_decision_oldest_ms: int = 0
    trade_intelligence_queue_experience_depth: int = 0
    trade_intelligence_queue_experience_oldest_ms: int = 0
    trade_intelligence_queue_paper_observation_depth: int = 0
    trade_intelligence_queue_paper_observation_oldest_ms: int = 0
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
    authoritative_stage: dict[str, object] = field(default_factory=dict)
    stream_observability: dict[str, object] = field(default_factory=dict)


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
            "research_events_coalesced": 0,
            "research_failures": 0,
            "processing_delayed_events": 0,
            "report_refresh_failures": 0,
            "latency_diagnostics_persisted": 0,
            "callback_threshold_events": 0,
            "runtime_projection_events_rejected": 0,
            # Constant-storage runtime proof that the first-class Quick Scalper
            # consumes shared intraminute data independently of Warrior's
            # bounded full-evaluation cadence.
            "quick_scalper_stream_observations": 0,
            "quick_scalper_assessments": 0,
            "quick_scalper_opportunities": 0,
            "quick_scalper_executable": 0,
            "quick_scalper_authorization_attempts": 0,
            "quick_scalper_rejections": 0,
            "quick_scalper_orders_submitted": 0,
            # Exact pre-bridge attribution. These counters distinguish
            # confirmation/sizing failures from the shared PAPER bridge.
            "quick_scalper_execution_quote_unavailable": 0,
            "quick_scalper_stale_preview_refresh_attempts": 0,
            "quick_scalper_stale_preview_refresh_available": 0,
            "quick_scalper_stale_preview_refresh_advanced": 0,
            "quick_scalper_reconfirm_not_executable": 0,
            "quick_scalper_account_unavailable": 0,
            "quick_scalper_risk_rejected": 0,
            "quick_scalper_insufficient_executable_size": 0,
            "quick_scalper_bridge_reached": 0,
            "quick_scalper_bridge_rejected": 0,
            "quick_scalper_bridge_authorized": 0,
            "quick_scalper_reconfirm_insufficient_edge": 0,
            "quick_scalper_reconfirm_provider_data_stale": 0,
            "quick_scalper_reconfirm_invalid_execution_quote": 0,
            "quick_scalper_reconfirm_session_not_allowed": 0,
            "quick_scalper_reconfirm_price_not_eligible": 0,
            "quick_scalper_reconfirm_risk_not_authorized": 0,
            "quick_scalper_reconfirm_other": 0,
            "quick_scalper_initial_insufficient_edge": 0,
            "quick_scalper_initial_provider_data_stale": 0,
            "quick_scalper_initial_invalid_execution_quote": 0,
            "quick_scalper_initial_session_not_allowed": 0,
            "quick_scalper_initial_price_not_eligible": 0,
            "quick_scalper_initial_risk_not_authorized": 0,
            "quick_scalper_initial_other": 0,
            "quick_scalper_risk_invalid_input": 0,
            "quick_scalper_risk_stop_distance": 0,
            "quick_scalper_risk_symbol_authorization": 0,
            "quick_scalper_risk_broker_restriction": 0,
            "quick_scalper_risk_engine": 0,
            "quick_scalper_risk_campaign_loss": 0,
            "quick_scalper_risk_zero_shares": 0,
            "quick_scalper_risk_exposure": 0,
            "quick_scalper_risk_other": 0,
            "quick_scalper_freshness_last_stale": 0,
            "quick_scalper_freshness_bid_stale": 0,
            "quick_scalper_freshness_ask_stale": 0,
            "quick_scalper_freshness_last_future": 0,
            "quick_scalper_freshness_bid_future": 0,
            "quick_scalper_freshness_ask_future": 0,
            "quick_scalper_freshness_last_only": 0,
            "quick_scalper_freshness_bid_only": 0,
            "quick_scalper_freshness_ask_only": 0,
            "quick_scalper_freshness_multiple": 0,
            "quick_scalper_freshness_last_age_ms_total": 0,
            "quick_scalper_freshness_bid_age_ms_total": 0,
            "quick_scalper_freshness_ask_age_ms_total": 0,
            "quick_scalper_freshness_last_age_ms_max": 0,
            "quick_scalper_freshness_bid_age_ms_max": 0,
            "quick_scalper_freshness_ask_age_ms_max": 0,
            "warrior_full_evaluations": 0,
            # Bounded Warrior fast-mover cadence/acceleration diagnostics.
            "fast_mover_refresh_eligible": 0,
            "fast_mover_refresh_due": 0,
            "fast_mover_refresh_executed": 0,
            "acceleration_point_appended": 0,
            "acceleration_point_duplicate_skipped": 0,
            "acceleration_insufficient_points": 0,
            "acceleration_lifetime_exceeded": 0,
            "acceleration_predicate_failed": 0,
            "acceleration_forming": 0,
            "acceleration_triggered": 0,
            "acceleration_masked_by_stronger_setup": 0,
            "reacceleration_insufficient_points": 0,
            "reacceleration_lifetime_exceeded": 0,
            "reacceleration_predicate_failed": 0,
            "reacceleration_forming": 0,
            "reacceleration_triggered": 0,
            "reacceleration_masked_by_stronger_setup": 0,
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
        self._authoritative_stage: dict[str, object] = {
            "stage": None,
            "symbol": None,
            "event_type": None,
            "started_at": None,
            "started_monotonic": None,
            "age_ms": 0.0,
        }
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
            "trade_intelligence_queue_discovery_depth": 0,
            "trade_intelligence_queue_discovery_oldest_ms": 0,
            "trade_intelligence_queue_bar_depth": 0,
            "trade_intelligence_queue_bar_oldest_ms": 0,
            "trade_intelligence_queue_decision_depth": 0,
            "trade_intelligence_queue_decision_oldest_ms": 0,
            "trade_intelligence_queue_experience_depth": 0,
            "trade_intelligence_queue_experience_oldest_ms": 0,
            "trade_intelligence_queue_paper_observation_depth": 0,
            "trade_intelligence_queue_paper_observation_oldest_ms": 0,
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
            "long_operation_count": 0,
            "long_operation_max_ms": 0.0,
            "last_long_operation_at": None,
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
        self._stream_observability_events: deque[dict[str, object]] = deque(
            maxlen=_MAX_STREAM_OBSERVABILITY_EVENTS
        )
        self._stream_radar_events: deque[dict[str, object]] = deque(
            maxlen=_MAX_RADAR_OBSERVABILITY_EVENTS
        )
        self._stream_observability_counts: dict[str, int] = {}
        self._active_recovery_controllers: set[str] = set()
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
            "readiness_counts": {},
            "missing_state_counts": {},
            "missing_state_examples": {},
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
        self._entry_funnel_counts = {name: 0 for name in _ENTRY_FUNNEL_STAGES}
        self._entry_funnel_records: deque[dict[str, object]] = deque(maxlen=_MAX_ENTRY_FUNNEL_RECORDS)
        self._entry_gate_counters = {name: 0 for name in _EXECUTION_GATE_COUNTERS}
        self._quote_age_samples = {name: deque(maxlen=2048) for name in _QUOTE_AGE_FIELDS}
        self._price_gate_samples: deque[dict[str, object]] = deque(maxlen=_MAX_PRICE_GATE_SAMPLES)
        self._price_gate_result_counters = {name: 0 for name in _PRICE_GATE_RESULT_CODES}
        self._price_gate_continuation_rejections = {
            name: 0 for name in _PRICE_GATE_CONTINUATION_CLASSES
        }
        self._price_gate_quantile_samples: dict[str, deque[float]] = {
            name: deque(maxlen=_MAX_PRICE_GATE_QUANTILE_SAMPLES)
            for name in _PRICE_GATE_DISTRIBUTION_FIELDS
        }
        self._price_gate_distribution_counts = {
            name: 0 for name in _PRICE_GATE_DISTRIBUTION_FIELDS
        }
        self._price_gate_by_setup: OrderedDict[str, dict[str, object]] = OrderedDict()
        self._detector_diagnostics_provider: object | None = None
        self._protection_events: deque[dict[str, object]] = deque(maxlen=_MAX_PROTECTION_EVENTS)
        self._management_events: deque[dict[str, object]] = deque(maxlen=_MAX_MANAGEMENT_EVENTS)
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
        self._shutdown_runtime_session_id: str | None = None
        self._shutdown_requested_at: str | None = None
        self._shutdown_origin: str | None = None
        self._shutdown_reason: str | None = None
        self._shutdown_component: str | None = None
        self._shutdown_stop_event_already_set = False
        self._shutdown_failure_present = False
        self._shutdown_operator_initiated = False
        self._shutdown_exception_class: str | None = None
        self._shutdown_cleanup_completed = False
        self._shutdown_completed_at: str | None = None
        self._shutdown_events: deque[dict[str, object]] = deque(
            maxlen=_MAX_SHUTDOWN_EVENTS
        )

    @property
    def run_id(self) -> str:
        """Return the current bounded process/runtime diagnostic identity."""
        with self._lock:
            return self._run_id

    @property
    def artifact_path(self) -> Path | None:
        with self._lock:
            return self._artifact_path

    def begin_runtime_session(self) -> str:
        """Begin bounded provenance for one runtime session."""
        session_id = uuid4().hex
        try:
            with self._lock:
                self._shutdown_runtime_session_id = session_id
                self._shutdown_requested_at = None
                self._shutdown_origin = None
                self._shutdown_reason = None
                self._shutdown_component = None
                self._shutdown_stop_event_already_set = False
                self._shutdown_failure_present = False
                self._shutdown_operator_initiated = False
                self._shutdown_exception_class = None
                self._shutdown_cleanup_completed = False
                self._shutdown_completed_at = None
        except Exception:
            return session_id
        self.request_checkpoint()
        return session_id

    def record_shutdown_request(
        self,
        *,
        origin: ShutdownOrigin | str,
        reason: ShutdownReason | str,
        component: object,
        stop_event_already_set: bool,
        failure_present: bool,
        operator_initiated: bool,
        runtime_session_id: str | None = None,
        exception_class: object | None = None,
        runtime_state: object | None = None,
    ) -> bool:
        """Persist the first shutdown request; later requests cannot replace it."""
        try:
            timestamp = datetime.now(UTC).isoformat()
            normalized_origin = _bounded_enum_value(
                origin, ShutdownOrigin, ShutdownOrigin.UNKNOWN
            )
            normalized_reason = _bounded_enum_value(
                reason, ShutdownReason, ShutdownReason.UNSPECIFIED
            )
            with self._lock:
                if self._shutdown_requested_at is not None:
                    return False
                session_id = _bounded_shutdown_text(
                    runtime_session_id or self._shutdown_runtime_session_id,
                    maximum=64,
                )
                self._shutdown_runtime_session_id = session_id
                self._shutdown_requested_at = timestamp
                self._shutdown_origin = normalized_origin
                self._shutdown_reason = normalized_reason
                self._shutdown_component = _bounded_shutdown_text(component)
                self._shutdown_stop_event_already_set = bool(stop_event_already_set)
                self._shutdown_failure_present = bool(failure_present)
                self._shutdown_operator_initiated = bool(operator_initiated)
                self._shutdown_exception_class = _bounded_shutdown_text(exception_class)
                self._shutdown_events.append({
                    "event_type": "RUNTIME_STOP_REQUESTED",
                    "timestamp": timestamp,
                    "origin": normalized_origin,
                    "reason": normalized_reason,
                    "component": self._shutdown_component,
                    "runtime_session_id": session_id,
                    "stop_event_already_set": bool(stop_event_already_set),
                    "failure_present": bool(failure_present),
                    "operator_initiated": bool(operator_initiated),
                    "cleanup_completed": False,
                    "exception_class": self._shutdown_exception_class,
                    "runtime_state": _bounded_shutdown_text(runtime_state),
                })
            self.request_checkpoint()
            return True
        except Exception:
            return False

    def record_shutdown_stopping(self, *, runtime_state: object | None = None) -> None:
        try:
            timestamp = datetime.now(UTC).isoformat()
            with self._lock:
                self._shutdown_events.append({
                    "event_type": "RUNTIME_STOPPING",
                    "timestamp": timestamp,
                    "origin": self._shutdown_origin or ShutdownOrigin.UNKNOWN.value,
                    "reason": self._shutdown_reason or ShutdownReason.UNSPECIFIED.value,
                    "component": self._shutdown_component,
                    "runtime_session_id": self._shutdown_runtime_session_id,
                    "stop_event_already_set": self._shutdown_stop_event_already_set,
                    "failure_present": self._shutdown_failure_present,
                    "operator_initiated": self._shutdown_operator_initiated,
                    "cleanup_completed": False,
                    "runtime_state": _bounded_shutdown_text(runtime_state),
                })
            self.request_checkpoint()
        except Exception:
            return

    def record_shutdown_stopped(
        self,
        *,
        cleanup_completed: bool,
        failure_present: bool | None = None,
    ) -> None:
        try:
            timestamp = datetime.now(UTC).isoformat()
            with self._lock:
                if failure_present is not None:
                    self._shutdown_failure_present = (
                        self._shutdown_failure_present
                        or bool(failure_present)
                    )
                self._shutdown_cleanup_completed = bool(cleanup_completed)
                self._shutdown_completed_at = timestamp
                self._shutdown_events.append({
                    "event_type": "RUNTIME_STOPPED",
                    "timestamp": timestamp,
                    "origin": self._shutdown_origin or ShutdownOrigin.UNKNOWN.value,
                    "reason": self._shutdown_reason or ShutdownReason.UNSPECIFIED.value,
                    "component": self._shutdown_component,
                    "runtime_session_id": self._shutdown_runtime_session_id,
                    "stop_event_already_set": self._shutdown_stop_event_already_set,
                    "failure_present": self._shutdown_failure_present,
                    "operator_initiated": self._shutdown_operator_initiated,
                    "cleanup_completed": bool(cleanup_completed),
                })
            self.request_checkpoint()
        except Exception:
            return

    def record_unexpected_consumer_termination(
        self,
        *,
        component: object,
        exception_class: object | None,
        runtime_state: object,
    ) -> None:
        try:
            with self._lock:
                self._shutdown_events.append({
                    "event_type": "UNEXPECTED_CONSUMER_TERMINATION",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "component": _bounded_shutdown_text(component),
                    "exception_class": _bounded_shutdown_text(exception_class),
                    "runtime_state": _bounded_shutdown_text(runtime_state),
                    "runtime_session_id": self._shutdown_runtime_session_id,
                })
            self.request_checkpoint()
        except Exception:
            return

    def shutdown_metrics(self) -> dict[str, object]:
        with self._lock:
            return {
                "shutdown_requested_at": self._shutdown_requested_at,
                "shutdown_origin": self._shutdown_origin,
                "shutdown_reason": self._shutdown_reason,
                "shutdown_component": self._shutdown_component,
                "shutdown_runtime_session_id": self._shutdown_runtime_session_id,
                "shutdown_stop_event_already_set": self._shutdown_stop_event_already_set,
                "shutdown_failure_present": self._shutdown_failure_present,
                "shutdown_operator_initiated": self._shutdown_operator_initiated,
                "shutdown_exception_class": self._shutdown_exception_class,
                "shutdown_cleanup_completed": self._shutdown_cleanup_completed,
                "shutdown_completed_at": self._shutdown_completed_at,
                "events": tuple(dict(event) for event in self._shutdown_events),
            }

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
            shutdown = self.shutdown_metrics()
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
                "shutdown_requested_at": shutdown["shutdown_requested_at"],
                "shutdown_origin": shutdown["shutdown_origin"],
                "shutdown_reason": shutdown["shutdown_reason"],
                "shutdown_component": shutdown["shutdown_component"],
                "shutdown_runtime_session_id": shutdown["shutdown_runtime_session_id"],
                "shutdown_stop_event_already_set": shutdown["shutdown_stop_event_already_set"],
                "shutdown_failure_present": shutdown["shutdown_failure_present"],
                "shutdown_operator_initiated": shutdown["shutdown_operator_initiated"],
                "shutdown_cleanup_completed": shutdown["shutdown_cleanup_completed"],
                "shutdown_completed_at": shutdown["shutdown_completed_at"],
                "shutdown": _json_safe(shutdown),
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

    def record_quick_scalper_freshness(
        self, *, last_age_ms: int, bid_age_ms: int, ask_age_ms: int,
        limit_ms: int,
    ) -> None:
        """Record bounded component-level freshness evidence for one rejection."""
        try:
            ages = {
                "last": int(last_age_ms),
                "bid": int(bid_age_ms),
                "ask": int(ask_age_ms),
            }
            limit = max(0, int(limit_ms))
            with self._lock:
                stale = []
                for name, age in ages.items():
                    if age < 0:
                        self._counters[f"quick_scalper_freshness_{name}_future"] += 1
                    elif age > limit:
                        stale.append(name)
                        self._counters[f"quick_scalper_freshness_{name}_stale"] += 1
                        self._counters[
                            f"quick_scalper_freshness_{name}_age_ms_total"
                        ] += age
                        maximum = f"quick_scalper_freshness_{name}_age_ms_max"
                        self._counters[maximum] = max(
                            self._counters[maximum], age,
                        )
                if len(stale) == 1:
                    self._counters[
                        f"quick_scalper_freshness_{stale[0]}_only"
                    ] += 1
                elif len(stale) > 1:
                    self._counters["quick_scalper_freshness_multiple"] += 1
        except Exception:
            return

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

    def record_price_gate_sample(self, *, symbol: str, setup_type: object,
                                 gate_result: str, **values: object) -> None:
        """Retain bounded, payload-free adaptive price-gate evidence.

        This method is deliberately diagnostic-only.  It never raises and does
        not participate in the price decision or execution authorization.
        Numeric distributions use bounded reservoir-like recent samples while
        exact result counters remain cumulative.
        """
        try:
            normalized_symbol = str(symbol).strip().upper()
            normalized_setup = getattr(setup_type, "value", setup_type)
            normalized_setup = str(normalized_setup or "UNKNOWN").strip().upper()[:64]
            normalized_result = str(gate_result or "OTHER").strip().upper()[:64]
            if normalized_result not in _PRICE_GATE_RESULT_CODES:
                normalized_result = "ENTRY_PRICE_INVALID"
            continuation = str(values.get("continuation_class") or "OTHER").strip().upper()
            if continuation not in _PRICE_GATE_CONTINUATION_CLASSES:
                continuation = "OTHER"

            # Lifecycle identifiers are intentionally hashed before publication.
            identity = values.get("lifecycle_id")
            if identity is not None:
                values = dict(values)
                values["lifecycle_id"] = sha256(str(identity).encode("utf-8")).hexdigest()[:16]
            record: dict[str, object] = {
                "symbol": normalized_symbol,
                "setup_type": normalized_setup,
                "gate_result": normalized_result,
                "continuation_class": continuation,
            }
            allowed = {
                "lifecycle_id", "evaluation_timestamp", "structural_trigger",
                "trigger_timestamp", "trigger_age_seconds", "entry_ready_timestamp",
                "entry_ready_age_seconds", "technical_signal_timestamp", "signal_age_seconds",
                "ask", "bid", "spread", "reference_price", "structural_stop",
                "base_displacement_percent", "contextual_displacement_percent",
                "effective_percent_limit", "absolute_outer_limit",
                "effective_max_execution_price", "actual_displacement_percent",
                "actual_displacement_dollars", "risk_per_share",
                "risk_normalized_extension", "remaining_first_target_r",
                "remaining_final_target_r",
            }
            for key in allowed:
                if key in values and values[key] is not None:
                    record[key] = _json_safe(values[key])

            with self._lock:
                self._price_gate_samples.append(record)
                self._price_gate_result_counters[normalized_result] += 1
                if normalized_result != "ACCEPTED":
                    self._price_gate_continuation_rejections[continuation] += 1
                for field_name in _PRICE_GATE_DISTRIBUTION_FIELDS:
                    try:
                        raw = values.get(field_name)
                        if raw is None:
                            continue
                        numeric = float(raw)
                        if numeric != numeric or numeric in (float("inf"), float("-inf")):
                            continue
                        self._price_gate_quantile_samples[field_name].append(numeric)
                        self._price_gate_distribution_counts[field_name] += 1
                    except (TypeError, ValueError):
                        continue
                bucket = self._price_gate_by_setup.get(normalized_setup)
                if bucket is None:
                    if len(self._price_gate_by_setup) >= _MAX_PRICE_GATE_SETUP_BREAKDOWNS:
                        self._price_gate_by_setup.popitem(last=False)
                    bucket = {
                        "result_counters": {name: 0 for name in _PRICE_GATE_RESULT_CODES},
                        "continuation_rejection_counters": {
                            name: 0 for name in _PRICE_GATE_CONTINUATION_CLASSES
                        },
                        "quantile_samples": {
                            name: deque(maxlen=_MAX_PRICE_GATE_QUANTILE_SAMPLES)
                            for name in _PRICE_GATE_DISTRIBUTION_FIELDS
                        },
                        "distribution_counts": {
                            name: 0 for name in _PRICE_GATE_DISTRIBUTION_FIELDS
                        },
                    }
                    self._price_gate_by_setup[normalized_setup] = bucket
                else:
                    self._price_gate_by_setup.move_to_end(normalized_setup)
                bucket["result_counters"][normalized_result] += 1
                if normalized_result != "ACCEPTED":
                    bucket["continuation_rejection_counters"][continuation] += 1
                for field_name in _PRICE_GATE_DISTRIBUTION_FIELDS:
                    try:
                        raw = values.get(field_name)
                        if raw is not None:
                            numeric = float(raw)
                            if numeric == numeric and numeric not in (float("inf"), float("-inf")):
                                bucket["quantile_samples"][field_name].append(numeric)
                                bucket["distribution_counts"][field_name] += 1
                    except (TypeError, ValueError):
                        continue
        except Exception:
            return

    def record_entry_funnel(self, symbol: str, stage: str,
                            outcome: str = "OBSERVED", timestamp: object | None = None,
                            reason: str | None = None, **values: object) -> None:
        """Record bounded, sanitized entry-funnel evidence.

        This is diagnostic-only. Unknown stages/reasons are normalized and no
        exception or provider payload is retained.
        """
        try:
            normalized_symbol = str(symbol).strip().upper()
            normalized_stage = str(stage).strip().upper()
            if not normalized_symbol or normalized_stage not in _ENTRY_FUNNEL_STAGES:
                return
            normalized_outcome = str(outcome).strip().upper()[:64] or "OBSERVED"
            effective_reason = (
                str(reason).strip().upper()[:64]
                if reason is not None
                else normalized_outcome
            )
            record = {
                "symbol": normalized_symbol,
                "stage": normalized_stage,
                "outcome": normalized_outcome,
            }
            if timestamp is not None:
                record["timestamp"] = _json_safe(timestamp)
            if reason is not None:
                record["reason"] = str(reason).strip().upper()[:64]
            if effective_reason in self._entry_gate_counters:
                record["reason"] = effective_reason
            normalized_reason = record.get("reason")
            if normalized_reason in self._entry_gate_counters:
                with self._lock:
                    self._entry_gate_counters[normalized_reason] += 1
            age_values = {
                "bid_age": values.get("bid_timestamp_age"),
                "last_trade_age": values.get("last_trade_timestamp_age"),
                "processing_age": values.get("processing_age"),
                "delivery_age": values.get("delivery_age"),
            }
            with self._lock:
                for age_name, age_value in age_values.items():
                    try:
                        if age_value is not None:
                            numeric = float(age_value)
                            if numeric >= 0 and numeric != float("inf"):
                                self._quote_age_samples[age_name].append(numeric)
                    except (TypeError, ValueError):
                        continue
            for key, value in values.items():
                if key in {"price", "quantity", "account", "response", "exception", "message"}:
                    continue
                record[str(key)] = _json_safe(value)
            with self._lock:
                self._entry_funnel_counts[normalized_stage] += 1
                self._entry_funnel_records.append(record)
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
                "selection_scoring",
                "premarket_context_state", "premarket_bar_count",
                "premarket_earliest", "premarket_latest",
                "premarket_decision_cutoff",
                "premarket_high_available", "premarket_consolidation_available",
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
            provider = self._detector_diagnostics_provider
            detector_diagnostics = {}
            detector_transitions = {}
            unique_episodes = {}
            if provider is not None:
                try:
                    detector_diagnostics = provider.snapshot()
                    detector_transitions = provider.all_transitions()
                    unique_episodes = provider.unique_episode_counts()
                except Exception:
                    detector_diagnostics = {}
                    detector_transitions = {}
                    unique_episodes = {}
            price_gate_by_setup = {}
            for setup_name, bucket in self._price_gate_by_setup.items():
                price_gate_by_setup[setup_name] = {
                    "result_counters": dict(bucket["result_counters"]),
                    "continuation_rejection_counters": dict(bucket["continuation_rejection_counters"]),
                    "distributions": {
                        field_name: dict(
                            _price_gate_distribution(tuple(samples)),
                            count=bucket["distribution_counts"][field_name],
                        )
                        for field_name, samples in bucket["quantile_samples"].items()
                    },
                }
            return {
                "counters": dict(self._entry_conversion_counters),
                "replacement_refusal_counts_by_reason": dict(self._entry_refusal_counts),
                "lifecycle_records": tuple(self._entry_lifecycle_records.values()),
                "pursuit_evaluations": tuple(self._entry_pursuit_records),
                "setup_transitions": tuple(self._entry_setup_records),
                "funnel_counts": dict(self._entry_funnel_counts),
                "funnel_records": tuple(self._entry_funnel_records),
                "execution_gate_counters": dict(self._entry_gate_counters),
                "quote_age_summary": {
                    name: _age_summary(tuple(values))
                    for name, values in self._quote_age_samples.items()
                },
                "price_gate_samples": tuple(self._price_gate_samples),
                "price_gate_result_counters": dict(self._price_gate_result_counters),
                "price_gate_continuation_rejection_counters": dict(self._price_gate_continuation_rejections),
                "price_gate_distributions": {
                    field_name: dict(
                        _price_gate_distribution(tuple(samples)),
                        count=self._price_gate_distribution_counts[field_name],
                    )
                    for field_name, samples in self._price_gate_quantile_samples.items()
                },
                "price_gate_by_setup_type": price_gate_by_setup,
                "detector_diagnostics": detector_diagnostics,
                "detector_transitions": detector_transitions,
                "unique_setup_episodes": {
                    name: values.get("unique_setup_episodes", 0)
                    for name, values in unique_episodes.items()
                },
                "unique_triggered_episodes": {
                    name: values.get("unique_triggered_episodes", 0)
                    for name, values in unique_episodes.items()
                },
                "bounds": {
                    "lifecycle_records": _MAX_ENTRY_LIFECYCLE_RECORDS,
                    "pursuit_evaluations": _MAX_ENTRY_PURSUIT_RECORDS,
                    "setup_transitions": _MAX_SETUP_TRANSITION_RECORDS,
                    "funnel_records": _MAX_ENTRY_FUNNEL_RECORDS,
                    "detector_symbols": 512,
                    "detector_transitions_per_symbol": 8,
                    "quote_age_samples_per_field": 2048,
                    "price_gate_samples": _MAX_PRICE_GATE_SAMPLES,
                    "price_gate_quantile_samples_per_field": _MAX_PRICE_GATE_QUANTILE_SAMPLES,
                    "price_gate_setup_breakdowns": _MAX_PRICE_GATE_SETUP_BREAKDOWNS,
                },
            }

    def register_detector_diagnostics(self, provider: object | None) -> None:
        """Register the bounded Warrior diagnostic store for artifact export."""
        try:
            with self._lock:
                self._detector_diagnostics_provider = provider
        except Exception:
            return

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

    def record_management_event(self, *, state: str, **values: object) -> None:
        """Record a bounded PAPER exit-management transition."""
        try:
            record = {"state": str(state).strip().upper()}
            record.update({
                str(key): _json_safe(value)
                for key, value in values.items() if value is not None
            })
            with self._lock:
                self._management_events.append(record)
        except Exception:
            return

    def reconciliation_metrics(self) -> dict[str, object]:
        with self._lock:
            result: dict[str, object] = dict(self._reconciliation_counters)
            result["protection_events"] = tuple(self._protection_events)
            result["protection_event_limit"] = _MAX_PROTECTION_EVENTS
            result["management_events"] = tuple(self._management_events)
            result["management_event_limit"] = _MAX_MANAGEMENT_EVENTS
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
            if duration_ms >= 250.0:
                metrics["long_operation_count"] = int(metrics["long_operation_count"]) + 1
                metrics["long_operation_max_ms"] = max(float(metrics["long_operation_max_ms"]), duration_ms)
                metrics["last_long_operation_at"] = datetime.now(UTC).isoformat()
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

    def record_stream_observability(
        self,
        event: str,
        *,
        sample: bool = True,
        timestamp: datetime | None = None,
        **fields: object,
    ) -> None:
        """Record bounded, sanitized stream evidence without affecting runtime behavior."""
        try:
            name = re.sub(r"[^A-Z0-9_.-]", "_", str(event).upper())[:80] or "UNKNOWN"
            with self._lock:
                if name not in self._stream_observability_counts and len(self._stream_observability_counts) >= 128:
                    name = "OTHER"
                self._stream_observability_counts[name] = (
                    self._stream_observability_counts.get(name, 0) + 1
                )
                if not sample:
                    return
                record: dict[str, object] = {
                    "event": name,
                    "timestamp": (timestamp or datetime.now(UTC)).isoformat(),
                }
                controller = str(fields.get("controller", "")).upper()
                if name == "RECOVERY_STARTED" and controller:
                    record["overlap_category"] = (
                        "RECOVERY_OVERLAP_DETECTED"
                        if self._active_recovery_controllers
                        else "RECOVERY_NO_OVERLAP"
                    )
                    self._active_recovery_controllers.add(controller)
                elif name in {"RECOVERY_SUCCEEDED", "RECOVERY_FAILED"} and controller:
                    self._active_recovery_controllers.discard(controller)
                for key, value in fields.items():
                    key_text = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(key))[:64]
                    if any(token in key_text.lower() for token in ("token", "secret", "password", "header", "body", "message", "authorization")):
                        continue
                    if isinstance(value, Exception):
                        value = type(value).__name__
                    if key_text in {"symbol", "normalized_symbol"} and isinstance(value, str):
                        value = value.strip().upper()[:24]
                    elif isinstance(value, str):
                        value = value[:128]
                    elif isinstance(value, (int, float, bool)) or value is None:
                        pass
                    else:
                        value = str(value)[:128]
                    record[key_text] = value
                if name == "RADAR_PROMOTED":
                    # Per-symbol promotion evidence is intentionally kept out
                    # of the critical lifecycle ring.  The separate bounded
                    # ring preserves samples without evicting stale/recovery
                    # evidence needed to explain transport outages.
                    self._stream_radar_events.append(record)
                else:
                    if len(self._stream_observability_events) >= _MAX_STREAM_OBSERVABILITY_EVENTS:
                        if name not in _CRITICAL_STREAM_EVENTS:
                            return
                        replacement = next(
                            (
                                index
                                for index, existing in enumerate(self._stream_observability_events)
                                if existing.get("event") not in _CRITICAL_STREAM_EVENTS
                            ),
                            None,
                        )
                        if replacement is None:
                            self._stream_observability_events.popleft()
                        else:
                            del self._stream_observability_events[replacement]
                    self._stream_observability_events.append(record)
        except Exception:
            # Diagnostics are strictly non-authoritative.
            return

    def stream_observability(self) -> dict[str, object]:
        with self._lock:
            return {
                "counts": dict(self._stream_observability_counts),
                "events": tuple(dict(item) for item in self._stream_observability_events),
                "radar_events": tuple(dict(item) for item in self._stream_radar_events),
            }

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
        readiness_counts: object = None,
        missing_state_counts: object = None,
        missing_state_examples: object = None,
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
            self._scanner_population["readiness_counts"] = {
                str(key): max(0, int(value)) if value is not None else None
                for key, value in dict(readiness_counts or {}).items()
            }
            self._scanner_population["missing_state_counts"] = {
                str(key): max(0, int(value))
                for key, value in dict(missing_state_counts or {}).items()
            }
            self._scanner_population["missing_state_examples"] = {
                str(key): tuple(str(symbol) for symbol in values)[:3]
                for key, values in dict(missing_state_examples or {}).items()
            }

    def record_scanner_population_display(self, values: dict[str, object]) -> None:
        """Replace bounded decision/display aggregates for the latest snapshot."""
        with self._lock:
            prior_displayed = int(self._scanner_population.get("displayed_candidate_count", 0) or 0)
            for key, value in values.items():
                if key == "top_sample":
                    self._scanner_population[key] = tuple(value)[:25]
                else:
                    self._scanner_population[key] = value
            displayed = int(self._scanner_population.get("displayed_candidate_count", 0) or 0)
            if displayed == 0 and prior_displayed != 0:
                self.record_stream_observability(
                    "SCANNER_ZERO_CANDIDATES",
                    sample=True,
                    active_symbols=self._scanner_population.get("active_symbols", 0),
                    evaluated=self._scanner_population.get("complete_decision_count", 0),
                    qualified=self._scanner_population.get("qualified_count", 0),
                    scanner_state="RUNNING",
                )

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
                if key not in self._component_last_diagnostic or now - last >= 5.0:
                    self._component_last_diagnostic[key] = now
                    diagnostic = {
                        "component": key,
                        "event_type": event_type,
                        "symbol": symbol,
                        "duration_ms": round(duration_ms, 3),
                        "success": bool(success),
                        "timestamp": datetime.now(UTC).isoformat(),
                        "thread_category": current_thread().name[:64],
                    }
        if diagnostic is not None:
            self._emit_diagnostic("slow_component", diagnostic)

    @contextmanager
    def authoritative_stage(
        self,
        stage: str,
        *,
        symbol: str | None = None,
        event_type: str | None = None,
    ):
        """Bounded, exception-contained timing for strict market-event stages."""
        started = monotonic()
        with self._lock:
            previous_stage = dict(self._authoritative_stage)
            self._authoritative_stage = {
                "stage": str(stage),
                "symbol": symbol,
                "event_type": event_type,
                "started_at": datetime.now(UTC).isoformat(),
                "started_monotonic": started,
                "age_ms": 0.0,
            }
        success = False
        try:
            yield
            success = True
        finally:
            elapsed_ms = max(0.0, (monotonic() - started) * 1000.0)
            try:
                self.record_component_duration(
                    f"authoritative.{stage}", elapsed_ms,
                    event_type=event_type,
                    symbol=symbol,
                    success=success,
                )
                if str(stage) == "authoritative_worker_event":
                    self.record_component_duration(
                        "authoritative_worker_event_complete", elapsed_ms,
                        event_type=event_type,
                        symbol=symbol,
                        success=success,
                    )
            finally:
                with self._lock:
                    self._authoritative_stage = previous_stage

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

    def update_trade_intelligence_queue_composition(
        self,
        values: dict[str, int],
    ) -> None:
        allowed = {
            "trade_intelligence_queue_discovery_depth",
            "trade_intelligence_queue_discovery_oldest_ms",
            "trade_intelligence_queue_bar_depth",
            "trade_intelligence_queue_bar_oldest_ms",
            "trade_intelligence_queue_decision_depth",
            "trade_intelligence_queue_decision_oldest_ms",
            "trade_intelligence_queue_experience_depth",
            "trade_intelligence_queue_experience_oldest_ms",
            "trade_intelligence_queue_paper_observation_depth",
            "trade_intelligence_queue_paper_observation_oldest_ms",
        }
        sanitized = {
            key: max(0, int(value))
            for key, value in values.items()
            if key in allowed
        }
        with self._lock:
            self._trade_intelligence.update(sanitized)

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
            authoritative_stage = dict(self._authoritative_stage)
            started_monotonic = authoritative_stage.get("started_monotonic")
            if isinstance(started_monotonic, (int, float)):
                authoritative_stage["age_ms"] = round(
                    max(0.0, (monotonic() - float(started_monotonic)) * 1000.0), 3
                )
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
                authoritative_stage=authoritative_stage,
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
                stream_observability={
                    "counts": dict(self._stream_observability_counts),
                    "events": tuple(
                        dict(item) for item in self._stream_observability_events
                    ),
                    "radar_events": tuple(
                        dict(item) for item in self._stream_radar_events
                    ),
                },
            )


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, int((len(values) - 1) * fraction)))
    return values[index]


def _age_summary(values: tuple[float, ...]) -> dict[str, object]:
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "min": ordered[0] if ordered else None,
        "p50": _percentile(ordered, 0.50) if ordered else None,
        "p90": _percentile(ordered, 0.90) if ordered else None,
        "max": ordered[-1] if ordered else None,
    }


def _price_gate_distribution(values: tuple[float, ...]) -> dict[str, object]:
    """Return bounded quantiles for price-gate evidence."""
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "p50": _percentile(ordered, 0.50) if ordered else None,
        "p75": _percentile(ordered, 0.75) if ordered else None,
        "p90": _percentile(ordered, 0.90) if ordered else None,
        "p95": _percentile(ordered, 0.95) if ordered else None,
        "max": ordered[-1] if ordered else None,
    }


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


def _bounded_shutdown_text(value: object, *, maximum: int = 96) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(value).strip())
    return normalized[:maximum] or None


def _bounded_enum_value(value: object, enum_type, fallback) -> str:
    candidate = getattr(value, "value", value)
    try:
        return enum_type(str(candidate).strip().upper()).value
    except (TypeError, ValueError):
        return fallback.value


def subscription_fingerprint(symbols: object) -> str:
    """Return a deterministic, non-reversible identity for a symbol set."""
    try:
        normalized = sorted({str(item).strip().upper() for item in symbols if str(item).strip()})
    except Exception:
        normalized = []
    return sha256("|".join(normalized).encode("utf-8")).hexdigest()[:16]


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
    "ShutdownOrigin",
    "ShutdownReason",
    "performance_diagnostics",
    "subscription_fingerprint",
]
