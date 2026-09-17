"""Bounded, opt-in forensic observations for SEC ticker-map acquisition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import json
import os
from pathlib import Path
import re
from threading import Lock
from typing import Callable, Protocol


MAX_EVENTS = 512
MAX_EVENT_BYTES = 4 * 1024
MAX_SESSION_BYTES = 1024 * 1024
_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_EXCEPTION_CLASS = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,63}$")


class SecDiagnosticEvent(StrEnum):
    TICKER_ENDPOINT_BEGIN = "TICKER_ENDPOINT_BEGIN"
    TICKER_ENDPOINT_COMPLETE = "TICKER_ENDPOINT_COMPLETE"
    TICKER_TRANSPORT_FAILURE = "TICKER_TRANSPORT_FAILURE"
    TICKER_JSON_CLASSIFIED = "TICKER_JSON_CLASSIFIED"
    TICKER_PARSE_RESULT = "TICKER_PARSE_RESULT"
    TICKER_MAP_APPLY_RESULT = "TICKER_MAP_APPLY_RESULT"
    RESOLVER_RECOVERY_RESULT = "RESOLVER_RECOVERY_RESULT"
    SOURCE_AVAILABLE_RESULT = "SOURCE_AVAILABLE_RESULT"
    TICKER_READY_RESULT = "TICKER_READY_RESULT"
    STARTUP_TARGET_CALLBACK_RESULT = "STARTUP_TARGET_CALLBACK_RESULT"


class SecDiagnosticEndpoint(StrEnum):
    EXCHANGE_TICKER_MAP = "EXCHANGE_TICKER_MAP"
    LEGACY_TICKER_MAP_FALLBACK = "LEGACY_TICKER_MAP_FALLBACK"


class SecDiagnosticResult(StrEnum):
    BEGIN = "BEGIN"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"


class SecDiagnosticJsonType(StrEnum):
    OBJECT = "OBJECT"
    ARRAY = "ARRAY"
    STRING = "STRING"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"
    NULL = "NULL"
    INVALID = "INVALID"


class SecDiagnosticContainerType(StrEnum):
    MAPPING = "MAPPING"
    SEQUENCE = "SEQUENCE"
    MISSING = "MISSING"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class SecDiagnosticRecord:
    """Closed, sanitized event payload; no arbitrary fields are accepted."""

    event: SecDiagnosticEvent
    endpoint: SecDiagnosticEndpoint | None = None
    attempt_count: int | None = None
    transport_category: str | None = None
    http_status: int | None = None
    response_bytes: int | None = None
    json_type: SecDiagnosticJsonType | None = None
    row_container_type: SecDiagnosticContainerType | None = None
    row_count: int | None = None
    result: SecDiagnosticResult | None = None
    exception_class: str | None = None


class SecAcquisitionObservabilitySink(Protocol):
    def emit(self, record: SecDiagnosticRecord) -> None: ...
    def close(self) -> None: ...


class NoOpSecAcquisitionObservabilitySink:
    def emit(self, record: SecDiagnosticRecord) -> None:
        return None

    def close(self) -> None:
        return None


NOOP_SEC_OBSERVABILITY = NoOpSecAcquisitionObservabilitySink()


def safe_emit(sink: object, record: SecDiagnosticRecord) -> None:
    """Prevent any sink implementation from entering acquisition control flow."""

    try:
        emit = getattr(sink, "emit", None)
        if callable(emit):
            emit(record)
    except Exception:
        return None


def safe_close(sink: object) -> None:
    try:
        close = getattr(sink, "close", None)
        if callable(close):
            close()
    except Exception:
        return None


class BoundedJsonlSecAcquisitionObservabilitySink:
    """Exclusive, append-only session sink with hard byte and event limits."""

    def __init__(
        self,
        root: str | Path,
        session_id: str,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._lock = Lock()
        self._clock = clock
        self._accepted = 0
        self._bytes = 0
        self._active = False
        self._path: Path | None = None
        try:
            if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
                return
            directory = Path(root)
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"d3-sec-acquisition-{session_id}.jsonl"
            with path.open("xb"):
                pass
            self._path = path
            self._active = True
        except Exception:
            self._active = False
            self._path = None

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def active(self) -> bool:
        return self._active

    def emit(self, record: SecDiagnosticRecord) -> None:
        try:
            with self._lock:
                if not self._active or self._path is None:
                    return
                if self._accepted >= MAX_EVENTS or self._bytes >= MAX_SESSION_BYTES:
                    self._active = False
                    return
                actual_size = self._path.stat().st_size
                self._bytes = max(self._bytes, actual_size)
                if self._bytes >= MAX_SESSION_BYTES:
                    self._active = False
                    return
                payload = self._payload(record)
                serialized = json.dumps(
                    payload, ensure_ascii=True, allow_nan=False,
                    separators=(",", ":"), sort_keys=True,
                ).encode("utf-8") + b"\n"
                if len(serialized) > MAX_EVENT_BYTES:
                    self._active = False
                    return
                if self._bytes + len(serialized) > MAX_SESSION_BYTES:
                    self._active = False
                    return
                with self._path.open("ab") as stream:
                    stream.write(serialized)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._accepted += 1
                self._bytes += len(serialized)
                if self._accepted >= MAX_EVENTS or self._bytes >= MAX_SESSION_BYTES:
                    self._active = False
        except Exception:
            self._active = False

    def close(self) -> None:
        try:
            with self._lock:
                self._active = False
        except Exception:
            self._active = False

    def _payload(self, record: SecDiagnosticRecord) -> dict[str, object]:
        if not isinstance(record, SecDiagnosticRecord):
            raise TypeError("diagnostic record is invalid")
        observed = self._clock().astimezone(UTC).isoformat(timespec="milliseconds")
        payload: dict[str, object] = {
            "schema_version": 1,
            "sequence": self._accepted + 1,
            "observed_at": observed,
            "event": record.event.value,
        }
        optional = {
            "endpoint": _enum_value(record.endpoint, SecDiagnosticEndpoint),
            "attempt_count": _bounded_integer(record.attempt_count, 0, 8),
            "transport_category": _transport_category(record.transport_category),
            "http_status": _bounded_integer(record.http_status, 100, 599),
            "response_bytes": _bounded_integer(record.response_bytes, 0, 16 * 1024 * 1024),
            "json_type": _enum_value(record.json_type, SecDiagnosticJsonType),
            "row_container_type": _enum_value(record.row_container_type, SecDiagnosticContainerType),
            "row_count": _bounded_integer(record.row_count, 0, 50_000),
            "result": _enum_value(record.result, SecDiagnosticResult),
            "exception_class": _exception_name(record.exception_class),
        }
        payload.update({key: value for key, value in optional.items() if value is not None})
        return payload


def create_sec_acquisition_observability_sink(configuration: object) -> SecAcquisitionObservabilitySink:
    """Return the no-op default unless one complete D3 session is explicit."""

    try:
        if not bool(getattr(configuration, "d3_diagnostics_enabled", False)):
            return NOOP_SEC_OBSERVABILITY
        root = getattr(configuration, "d3_diagnostics_root", None)
        session_id = getattr(configuration, "d3_diagnostics_session_id", None)
        if root is None or session_id is None:
            return NOOP_SEC_OBSERVABILITY
        sink = BoundedJsonlSecAcquisitionObservabilitySink(root, session_id)
        return sink if sink.active else NOOP_SEC_OBSERVABILITY
    except Exception:
        return NOOP_SEC_OBSERVABILITY


def classify_ticker_json(content: bytes) -> tuple[SecDiagnosticJsonType, SecDiagnosticContainerType, int | None]:
    """Describe shape only; never return or retain response values."""

    try:
        value = json.loads(content)
    except Exception:
        return SecDiagnosticJsonType.INVALID, SecDiagnosticContainerType.INVALID, None
    json_type = _json_type(value)
    if isinstance(value, dict):
        rows = value.get("data", value)
    else:
        rows = value
    if isinstance(rows, dict):
        return json_type, SecDiagnosticContainerType.MAPPING, min(len(rows), 50_000)
    if isinstance(rows, list):
        return json_type, SecDiagnosticContainerType.SEQUENCE, min(len(rows), 50_000)
    if isinstance(value, dict) and "data" not in value:
        return json_type, SecDiagnosticContainerType.MAPPING, min(len(value), 50_000)
    return json_type, SecDiagnosticContainerType.MISSING, None


def exception_class_name(error: BaseException) -> str | None:
    return _exception_name(type(error).__name__)


def _json_type(value: object) -> SecDiagnosticJsonType:
    if value is None:
        return SecDiagnosticJsonType.NULL
    if isinstance(value, bool):
        return SecDiagnosticJsonType.BOOLEAN
    if isinstance(value, dict):
        return SecDiagnosticJsonType.OBJECT
    if isinstance(value, list):
        return SecDiagnosticJsonType.ARRAY
    if isinstance(value, str):
        return SecDiagnosticJsonType.STRING
    return SecDiagnosticJsonType.NUMBER


def _enum_value(value: object, kind: type[StrEnum]) -> str | None:
    return value.value if isinstance(value, kind) else None


def _bounded_integer(value: object, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return min(maximum, max(minimum, value))


def _transport_category(value: object) -> str | None:
    allowed = {
        "TIMEOUT", "CONNECTION_ERROR", "HTTP_RATE_LIMITED", "HTTP_SERVER_ERROR",
        "HTTP_CLIENT_ERROR", "MALFORMED_RESPONSE", "OVERSIZED_RESPONSE",
        "COOLDOWN_ACTIVE", "DISABLED", "INVALID_REQUEST", "UNKNOWN",
    }
    return value if isinstance(value, str) and value in allowed else None


def _exception_name(value: object) -> str | None:
    return value if isinstance(value, str) and _EXCEPTION_CLASS.fullmatch(value) else None


__all__ = [
    "BoundedJsonlSecAcquisitionObservabilitySink", "MAX_EVENTS", "MAX_EVENT_BYTES",
    "MAX_SESSION_BYTES", "NOOP_SEC_OBSERVABILITY", "NoOpSecAcquisitionObservabilitySink",
    "SecAcquisitionObservabilitySink", "SecDiagnosticContainerType", "SecDiagnosticEndpoint",
    "SecDiagnosticEvent", "SecDiagnosticJsonType", "SecDiagnosticRecord", "SecDiagnosticResult",
    "classify_ticker_json", "create_sec_acquisition_observability_sink", "exception_class_name",
    "safe_close", "safe_emit",
]
