from __future__ import annotations

from datetime import date
from time import perf_counter

from app.performance_diagnostics import performance_diagnostics

from .forward_queue import ForwardCaptureWriter
from .report_worker import WarriorReportWorker


def flush_capture_writer(writer: ForwardCaptureWriter) -> None:
    """Flush capture persistence while recording non-authoritative timing."""

    started = perf_counter()
    try:
        writer.flush()
    finally:
        duration_ms = (perf_counter() - started) * 1000.0
        performance_diagnostics.record_completed_bar_flush_duration(duration_ms)
        performance_diagnostics.mark_latency_trace_stage(
            "completed_bar_flush_duration_ms",
            duration_ms,
        )


def request_report_refresh(
    worker: WarriorReportWorker | None,
    *,
    trading_date: date,
    configuration_fingerprint: str,
    persist: bool = False,
) -> None:
    """Request report work without changing Warrior trading authority."""

    if worker is None:
        return
    started = perf_counter()
    try:
        worker.request_refresh(
            trading_date,
            configuration_fingerprint=configuration_fingerprint,
            persist=persist,
        )
    finally:
        duration_ms = (perf_counter() - started) * 1000.0
        performance_diagnostics.record_report_request_duration(duration_ms)
        performance_diagnostics.mark_latency_trace_stage(
            "report_refresh_request_duration_ms",
            duration_ms,
        )


__all__ = [
    "flush_capture_writer",
    "request_report_refresh",
]
