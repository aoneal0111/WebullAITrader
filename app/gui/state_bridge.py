from __future__ import annotations

import logging
from threading import Lock
from time import perf_counter

from PySide6.QtCore import QObject, QTimer, Signal

from app.operations_core import ApplicationState, ApplicationStateStore
from app.performance_diagnostics import (
    PerformanceDiagnostics,
    performance_diagnostics,
)


_LOGGER = logging.getLogger("atlas.gui.performance")


class QtStateBridge(QObject):
    """
    Delivers ApplicationState snapshots to Qt widgets safely.

    ApplicationStateStore listeners may run on a worker thread. Emitting a Qt
    signal allows Qt to queue delivery to widgets on the GUI thread.
    """

    state_changed = Signal(object)

    def __init__(
        self,
        state_store: ApplicationStateStore,
        parent: QObject | None = None,
        *,
        refresh_interval_ms: int = 125,
        diagnostics: PerformanceDiagnostics = performance_diagnostics,
    ) -> None:
        super().__init__(parent)
        if refresh_interval_ms < 1:
            raise ValueError("refresh_interval_ms must be positive")

        self._state_store = state_store
        self._diagnostics = diagnostics
        self._lock = Lock()
        self._latest_state: ApplicationState | None = None
        self._last_revision = -1
        self._last_flush_at = perf_counter()
        self._last_log_at = self._last_flush_at
        self._listener_id = state_store.subscribe(self._forward_state)
        self._timer = QTimer(self)
        self._timer.setInterval(refresh_interval_ms)
        self._timer.timeout.connect(self._flush)
        self._timer.start()

    def close(self) -> None:
        self._timer.stop()
        self._state_store.unsubscribe(self._listener_id)
        with self._lock:
            self._latest_state = None
        self._diagnostics.set_pending_gui_updates(0)

    def _forward_state(self, state: ApplicationState) -> None:
        # This callback runs on the publishing worker. Keeping only the newest
        # immutable snapshot makes the worker-to-GUI queue strictly bounded.
        with self._lock:
            self._latest_state = state
        self._diagnostics.set_pending_gui_updates(1)

    def _flush(self) -> None:
        with self._lock:
            state = self._latest_state
            self._latest_state = None
        self._diagnostics.set_pending_gui_updates(0)
        if state is None or state.revision == self._last_revision:
            return

        started = perf_counter()
        interval = started - self._last_flush_at
        self.state_changed.emit(state)
        duration_ms = (perf_counter() - started) * 1000.0
        self._last_flush_at = started
        self._last_revision = state.revision
        self._diagnostics.record_gui_refresh(duration_ms, interval)

        if started - self._last_log_at >= 5.0:
            metrics = self._diagnostics.snapshot()
            _LOGGER.info(
                "gui_refresh_hz=%.2f gui_refresh_duration_ms=%.2f "
                "gui_refresh_duration_avg_ms=%.2f "
                "gui_refresh_duration_max_ms=%.2f pending_gui_updates=%d "
                "market_events_received=%d market_events_processed=%d "
                "scanner_evaluations=%d scanner_snapshots_generated=%d "
                "scanner_snapshots_published=%d "
                "scanner_snapshots_suppressed_unchanged=%d "
                "stale_events_skipped=%d event_store_rows_added=%d "
                "callback_queue_depth=%d callback_queue_high_water=%d "
                "market_arrival_rate_hz=%.2f market_processing_rate_hz=%.2f "
                "event_processing_age_p50_ms=%.2f "
                "event_processing_age_p90_ms=%.2f "
                "event_processing_age_p99_ms=%.2f "
                "event_processing_age_max_ms=%.2f "
                "research_queue_depth=%d research_queue_high_water=%d "
                "research_worker_lag_max_ms=%.2f research_failures=%d "
                "processing_delayed_events=%d "
                "scanner_duration_ms=%.2f scanner_duration_max_ms=%.2f "
                "experiment_enqueue_ms=%.2f experiment_enqueue_max_ms=%.2f "
                "observer_duration_ms=%.2f observer_duration_max_ms=%.2f "
                "completed_bar_flush_ms=%.2f completed_bar_flush_max_ms=%.2f "
                "report_request_ms=%.2f report_request_max_ms=%.2f "
                "report_build_ms=%.2f report_build_max_ms=%.2f "
                "projection_ms=%.2f projection_max_ms=%.2f "
                "report_refresh_failures=%d latency_diagnostics=%d "
                "callback_threshold_events=%d",
                metrics.gui_refresh_hz,
                metrics.gui_refresh_duration_ms,
                metrics.gui_refresh_duration_avg_ms,
                metrics.gui_refresh_duration_max_ms,
                metrics.pending_gui_updates,
                metrics.market_events_received,
                metrics.market_events_processed,
                metrics.scanner_evaluations,
                metrics.scanner_snapshots_generated,
                metrics.scanner_snapshots_published,
                metrics.scanner_snapshots_suppressed_unchanged,
                metrics.stale_events_skipped,
                metrics.event_store_rows_added,
                metrics.callback_queue_depth,
                metrics.callback_queue_high_water,
                metrics.market_arrival_rate_hz,
                metrics.market_processing_rate_hz,
                metrics.event_processing_age_p50_ms,
                metrics.event_processing_age_p90_ms,
                metrics.event_processing_age_p99_ms,
                metrics.event_processing_age_max_ms,
                metrics.research_queue_depth,
                metrics.research_queue_high_water,
                metrics.research_worker_lag_max_ms,
                metrics.research_failures,
                metrics.processing_delayed_events,
                metrics.scanner_duration_ms,
                metrics.scanner_duration_max_ms,
                metrics.experiment_capture_duration_ms,
                metrics.experiment_capture_duration_max_ms,
                metrics.observer_duration_ms,
                metrics.observer_duration_max_ms,
                metrics.completed_bar_flush_duration_ms,
                metrics.completed_bar_flush_duration_max_ms,
                metrics.report_request_duration_ms,
                metrics.report_request_duration_max_ms,
                metrics.report_build_duration_ms,
                metrics.report_build_duration_max_ms,
                metrics.projection_duration_ms,
                metrics.projection_duration_max_ms,
                metrics.report_refresh_failures,
                metrics.latency_diagnostics_persisted,
                metrics.callback_threshold_events,
            )
            self._last_log_at = started
