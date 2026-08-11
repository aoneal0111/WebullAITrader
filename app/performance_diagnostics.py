"""Bounded, payload-free performance counters for the Atlas runtime."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock


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
        }
        self._pending_gui_updates = 0
        self._maximum_pending_gui_updates = 0
        self._gui_duration_total_ms = 0.0
        self._gui_duration_max_ms = 0.0
        self._gui_duration_latest_ms = 0.0
        self._gui_interval_seconds = 0.0

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
            )


performance_diagnostics = PerformanceDiagnostics()


__all__ = [
    "PerformanceDiagnostics",
    "PerformanceSnapshot",
    "performance_diagnostics",
]
