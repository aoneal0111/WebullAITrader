from __future__ import annotations

from datetime import datetime
import re

from app.market.calendar import EASTERN

from .forward_models import CAPTURE_SCHEMA_VERSION, CaptureRecord, CaptureRecordType
from .forward_queue import ForwardCaptureWriter


def build_session_record(
    *,
    writer: ForwardCaptureWriter | None,
    strategy_version: str,
    action: str,
    now: datetime,
    started_at: datetime | None,
    environment: str,
    configuration_fingerprint: str,
    run_key: str | None,
) -> CaptureRecord:
    """Build a capture-session record without coupling it to the desktop sidecar."""

    metrics = None if writer is None else writer.metrics()
    return CaptureRecord.create(
        CaptureRecordType.OBSERVATION_SESSION,
        strategy_version,
        now,
        {
            "action": action,
            "strategy_version": strategy_version,
            "schema_version": CAPTURE_SCHEMA_VERSION,
            "trading_date": now.astimezone(EASTERN).date(),
            "capture_start": started_at,
            "capture_end": now if action == "END" else None,
            "environment": environment,
            "configuration_fingerprint": configuration_fingerprint,
            "observation_run_key": run_key,
            "capture_metrics": (
                None
                if metrics is None
                else {
                    "queue_depth": metrics.queue_depth,
                    "records_written": metrics.records_written,
                    "average_write_latency_ms": metrics.average_write_latency_ms,
                    "maximum_write_latency_ms": metrics.maximum_write_latency_ms,
                    "dropped_records": metrics.dropped_records,
                    "duplicate_records": metrics.duplicate_records,
                    "synchronous_fallback_records": (
                        metrics.synchronous_fallback_records
                    ),
                    "gui_refresh_frequency_hz": (
                        metrics.gui_refresh_frequency_hz
                    ),
                }
            ),
        },
        identity_parts=(action, run_key or "unstarted"),
    )


def build_latency_diagnostic_record(
    *,
    kind: str,
    payload: dict[str, object],
    fallback_timestamp: datetime,
) -> CaptureRecord:
    """Translate diagnostic telemetry into a capture record."""

    enriched = {"diagnostic_kind": kind, **payload}
    timestamp_value = enriched.get("recorded_at") or enriched.get("timestamp")
    timestamp = (
        datetime.fromisoformat(str(timestamp_value))
        if timestamp_value is not None
        else fallback_timestamp
    )
    symbol = str(enriched.get("symbol") or "MARKET_DATA")
    record_type = (
        CaptureRecordType.CALLBACK_QUEUE_THRESHOLD
        if kind == "callback_queue_threshold"
        else CaptureRecordType.LATENCY_DIAGNOSTIC
    )
    identity = tuple(
        str(enriched.get(name) or "")
        for name in (
            "source",
            "sequence",
            "threshold",
            "direction",
            "recorded_at",
        )
    )
    return CaptureRecord.create(
        record_type,
        symbol,
        timestamp,
        enriched,
        identity_parts=identity,
    )


def safe_diagnostic_message(error: Exception) -> str:
    """Sanitize arbitrary exception text before diagnostic persistence."""

    message = str(error).replace("\r", " ").replace("\n", " ")
    message = re.sub(
        (
            r"(?i)(token|secret|password|credential|account[_ -]?id|"
            r"api[_ -]?key|access[_ -]?key)\s*[:=]\s*\S+"
        ),
        r"\1=<redacted>",
        message,
    )
    message = re.sub(r"(?i)bearer\s+\S+", "Bearer <redacted>", message)
    return message[:256] if message else "<empty>"


__all__ = [
    "build_latency_diagnostic_record",
    "build_session_record",
    "safe_diagnostic_message",
]
