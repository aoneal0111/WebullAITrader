"""Opt-in, bounded execution-quote provenance diagnostics.

This module is deliberately outside the trading decision path. Its default
sink is a no-op and every enabled write is exception-contained.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Mapping, Protocol
from uuid import uuid4


SCHEMA_VERSION = 1
MAX_EVENTS = 512
MAX_EVENT_BYTES = 4 * 1024
MAX_FILE_BYTES = 1024 * 1024
ENABLED_ENVIRONMENT_VARIABLE = "ATLAS_EXECUTION_QUOTE_PROVENANCE_ENABLED"
DIRECTORY_ENVIRONMENT_VARIABLE = "ATLAS_EXECUTION_QUOTE_PROVENANCE_DIRECTORY"
SESSION_ENVIRONMENT_VARIABLE = "ATLAS_EXECUTION_QUOTE_PROVENANCE_SESSION_ID"

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SAFE_SYMBOL = re.compile(r"^[A-Za-z0-9.-]{1,32}$")
_SAFE_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
_SAFE_TIMESTAMP = re.compile(
    r"^(?:[+-]?\d+(?:\.\d+)?|\d{4}-\d{2}-\d{2}T[0-9:.+-]+Z?)$"
)

_ALLOWED_FIELDS = frozenset({
    "schema_version", "event_type", "diagnostic_session_id", "request_id",
    "timestamp", "symbol", "request_timestamp", "snapshot_return_timestamp",
    "evaluation_timestamp", "raw_row_type", "raw_row_category", "raw_symbol",
    "raw_price", "raw_bid", "raw_ask", "raw_last_trade_time", "raw_quote_time",
    "has_bid_time", "has_ask_time", "has_update_time", "has_timestamp",
    "has_trade_time", "parsed_last_timestamp", "parsed_bid_timestamp",
    "parsed_ask_timestamp", "parsed_last", "parsed_bid", "parsed_ask",
    "calculated_last_age_seconds", "calculated_bid_age_seconds",
    "calculated_ask_age_seconds", "final_outcome", "rejection_reason",
    "parse_status", "exception_class",
})
_SYMBOL_FIELDS = frozenset({"symbol", "raw_symbol"})
_TOKEN_FIELDS = frozenset({
    "event_type", "diagnostic_session_id", "request_id", "raw_row_type",
    "raw_row_category", "final_outcome", "rejection_reason", "parse_status",
    "exception_class",
})
_NUMERIC_FIELDS = frozenset({
    "raw_price", "raw_bid", "raw_ask", "parsed_last", "parsed_bid", "parsed_ask",
    "calculated_last_age_seconds", "calculated_bid_age_seconds",
    "calculated_ask_age_seconds",
})
_TIMESTAMP_FIELDS = frozenset({
    "timestamp", "request_timestamp", "snapshot_return_timestamp",
    "evaluation_timestamp", "raw_last_trade_time", "raw_quote_time",
    "parsed_last_timestamp", "parsed_bid_timestamp", "parsed_ask_timestamp",
})
_BOOLEAN_FIELDS = frozenset({
    "has_bid_time", "has_ask_time", "has_update_time", "has_timestamp",
    "has_trade_time",
})


class ExecutionQuoteProvenanceSink(Protocol):
    enabled: bool

    def record(self, event: Mapping[str, object]) -> None:
        """Record one allowlisted event without raising into the caller."""


class NoOpExecutionQuoteProvenanceSink:
    enabled = False

    def record(self, event: Mapping[str, object]) -> None:
        return None


NOOP_EXECUTION_QUOTE_PROVENANCE = NoOpExecutionQuoteProvenanceSink()


class JsonlExecutionQuoteProvenanceSink:
    """Append-only JSONL sink that stops permanently when any bound is met."""

    enabled = True

    def __init__(
        self,
        directory: Path,
        session_id: str,
        *,
        maximum_events: int = MAX_EVENTS,
        maximum_event_bytes: int = MAX_EVENT_BYTES,
        maximum_file_bytes: int = MAX_FILE_BYTES,
    ) -> None:
        self.session_id = _safe_identifier(session_id) or uuid4().hex
        self.path = Path(directory) / (
            f"atlas-execution-quote-provenance-{self.session_id}.jsonl"
        )
        self.maximum_events = max(0, int(maximum_events))
        self.maximum_event_bytes = max(0, int(maximum_event_bytes))
        self.maximum_file_bytes = max(0, int(maximum_file_bytes))
        self._events_written = 0
        self._bytes_written = 0
        self._stopped = False
        self._initialized = False
        self._lock = threading.Lock()

    @property
    def stopped(self) -> bool:
        return self._stopped

    @property
    def events_written(self) -> int:
        return self._events_written

    @property
    def bytes_written(self) -> int:
        return self._bytes_written

    def record(self, event: Mapping[str, object]) -> None:
        try:
            sanitized = _sanitize_event(event, session_id=self.session_id)
            payload = (
                json.dumps(sanitized, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
            with self._lock:
                if self._stopped:
                    return
                if (
                    self._events_written >= self.maximum_events
                    or len(payload) > self.maximum_event_bytes
                    or self._bytes_written + len(payload) > self.maximum_file_bytes
                ):
                    self._stopped = True
                    return
                if not self._initialized:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    # Never append to or overwrite completed forensic evidence.
                    with self.path.open("xb"):
                        pass
                    self._initialized = True
                with self.path.open("ab") as stream:
                    stream.write(payload)
                    stream.flush()
                self._events_written += 1
                self._bytes_written += len(payload)
        except Exception:
            # Observability must never affect quote acquisition or evaluation.
            try:
                self._stopped = True
            except Exception:
                pass


def create_execution_quote_provenance_sink_from_environment(
    environment: Mapping[str, str] | None = None,
) -> ExecutionQuoteProvenanceSink:
    """Return an enabled sink only for the explicit true-valued opt-in flag."""

    try:
        values = os.environ if environment is None else environment
        if str(values.get(ENABLED_ENVIRONMENT_VARIABLE, "false")).strip().lower() != "true":
            return NOOP_EXECUTION_QUOTE_PROVENANCE
        session_id = str(values.get(SESSION_ENVIRONMENT_VARIABLE, "")).strip() or uuid4().hex
        directory_value = str(values.get(DIRECTORY_ENVIRONMENT_VARIABLE, "")).strip()
        directory = Path(directory_value) if directory_value else Path(
            values.get("TEMP", ".")
        )
        return JsonlExecutionQuoteProvenanceSink(directory, session_id)
    except Exception:
        return NOOP_EXECUTION_QUOTE_PROVENANCE


def _safe_identifier(value: object) -> str | None:
    text = str(value).strip()
    return text if _SAFE_TOKEN.fullmatch(text) else None


def _timestamp(value: object) -> str | None:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    return text if len(text) <= 64 and _SAFE_TIMESTAMP.fullmatch(text) else None


def _number(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (str, int, float, Decimal)):
        return None
    text = str(value).strip()
    return text if len(text) <= 64 and _SAFE_NUMBER.fullmatch(text) else None


def _sanitize_event(event: Mapping[str, object], *, session_id: str) -> dict[str, object]:
    sanitized: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "diagnostic_session_id": session_id,
    }
    for key in _ALLOWED_FIELDS:
        if key in {"schema_version", "diagnostic_session_id"} or key not in event:
            continue
        value = event[key]
        if value is None:
            continue
        cleaned: object | None
        if key in _SYMBOL_FIELDS:
            text = str(value).strip().upper()
            cleaned = text if _SAFE_SYMBOL.fullmatch(text) else None
        elif key in _TOKEN_FIELDS:
            cleaned = _safe_identifier(value)
        elif key in _NUMERIC_FIELDS:
            cleaned = _number(value)
        elif key in _TIMESTAMP_FIELDS:
            cleaned = _timestamp(value)
        elif key in _BOOLEAN_FIELDS:
            cleaned = value if isinstance(value, bool) else None
        else:
            cleaned = None
        if cleaned is not None:
            sanitized[key] = cleaned
    return sanitized


__all__ = [
    "DIRECTORY_ENVIRONMENT_VARIABLE",
    "ENABLED_ENVIRONMENT_VARIABLE",
    "ExecutionQuoteProvenanceSink",
    "JsonlExecutionQuoteProvenanceSink",
    "MAX_EVENTS",
    "MAX_EVENT_BYTES",
    "MAX_FILE_BYTES",
    "NOOP_EXECUTION_QUOTE_PROVENANCE",
    "NoOpExecutionQuoteProvenanceSink",
    "SCHEMA_VERSION",
    "SESSION_ENVIRONMENT_VARIABLE",
    "create_execution_quote_provenance_sink_from_environment",
]
