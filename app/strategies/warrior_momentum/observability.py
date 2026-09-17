"""Bounded, opt-in observations for the Warrior Momentum experiment."""
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

MAX_EVENTS = 2048
MAX_EVENT_BYTES = 4 * 1024
MAX_SESSION_BYTES = 2 * 1024 * 1024
_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")

# These are deliberately per-field allow-lists.  The diagnostic format is a
# forensic contract, so a bounded arbitrary string is not accepted merely
# because it happens to match the filename-safe token grammar.
_SESSIONS = frozenset({"PREMARKET", "REGULAR", "AFTER_HOURS", "OVERNIGHT", "PREPARATION", "UNKNOWN"})
_QUALIFICATIONS = frozenset({"QUALIFIED", "TECHNICAL_ONLY", "NOT_QUALIFIED"})
_SETUPS = frozenset({"HIGH_OF_DAY_BREAKOUT", "MICRO_PULLBACK", "BULL_FLAG", "FLAT_TOP_BREAKOUT"})
_ADMISSIONS = frozenset({"ADMITTED", "REJECTED"})
_ADMISSION_REASONS = frozenset({"PARENT_ADMISSION_FAILED", "QUEUE_FULL", "QUEUE_NOT_ACCEPTING", "DUPLICATE_SUPPRESSED", "NO_SETUP", "SETUP_STATE_NOT_ELIGIBLE", "TAXONOMY_ADAPTATION_FAILED"})
_FOCUS_ACTIONS = frozenset({"FIRST_INSERT", "REPLACE_EXISTING", "TOP_N_OMITTED", "DROPPED_FROM_PRIOR_SNAPSHOT", "NO_VIEW", "LOOKUP_MISS", "MATCHED"})
_RESULTS = frozenset({
    "ENTRY_READY", "ERROR", "DISCOVERED", "WATCH", "NEAR_QUALIFIED", "QUALIFIED",
    "SETUP_FORMING", "AWAITING_EXECUTION_DATA", "INELIGIBLE_FOR_EXECUTION",
    "ADMITTED", "REJECTED", *_FOCUS_ACTIONS,
})
_PHASES = frozenset({"SCANNER", "SETUP", "WARRIOR", "ADMISSION", "DECISION", "PERSISTENCE", "FOCUS", "GUI"})

class WarriorDiagnosticEvent(StrEnum):
    SCANNER_SEEN = "SCANNER_SEEN"
    SCANNER_QUALIFICATION_RESULT = "SCANNER_QUALIFICATION_RESULT"
    SETUP_RESULT = "SETUP_RESULT"
    WARRIOR_ADMISSION_RESULT = "WARRIOR_ADMISSION_RESULT"
    WARRIOR_EVALUATOR_INVOKED = "WARRIOR_EVALUATOR_INVOKED"
    WARRIOR_DECISION_RESULT = "WARRIOR_DECISION_RESULT"
    DECISION_PERSISTENCE_RESULT = "DECISION_PERSISTENCE_RESULT"
    FOCUS_PROJECTION_INSERT = "FOCUS_PROJECTION_INSERT"
    FOCUS_PROJECTION_OMIT = "FOCUS_PROJECTION_OMIT"
    FOCUS_PROJECTION_REPLACE = "FOCUS_PROJECTION_REPLACE"
    GUI_FOCUS_LOOKUP_RESULT = "GUI_FOCUS_LOOKUP_RESULT"

@dataclass(frozen=True, slots=True)
class WarriorDiagnosticRecord:
    event: WarriorDiagnosticEvent
    phase: str | None = None
    symbol: str | None = None
    session: str | None = None
    result: str | None = None
    reason: str | None = None
    count: int | None = None
    latency_ms: int | None = None
    exception_class: str | None = None
    qualification: str | None = None
    setup: str | None = None
    admission: str | None = None

class WarriorObservabilitySink(Protocol):
    def emit(self, record: WarriorDiagnosticRecord) -> None: ...
    def emit_warrior(self, **kwargs: object) -> None: ...
    def close(self) -> None: ...

class NoOpWarriorObservabilitySink:
    def emit(self, record: WarriorDiagnosticRecord) -> None: return None
    def close(self) -> None: return None
    def emit_warrior(self, **kwargs: object) -> None: return None

NOOP_WARRIOR_OBSERVABILITY = NoOpWarriorObservabilitySink()

def safe_emit(sink: object, record: WarriorDiagnosticRecord) -> None:
    try:
        emit = getattr(sink, "emit", None)
        if callable(emit): emit(record)
    except Exception: return None

def safe_close(sink: object) -> None:
    try:
        close = getattr(sink, "close", None)
        if callable(close): close()
    except Exception: return None

class BoundedJsonlWarriorObservabilitySink:
    """Exclusive append-only JSONL sink with hard event and byte bounds."""
    def __init__(self, root: str | Path, session_id: str,
                 *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._lock, self._clock = Lock(), clock
        self._accepted = self._bytes = 0
        self._active, self._path = False, None
        try:
            if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id): return
            directory = Path(root); directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"warrior-observability-{session_id}.jsonl"
            with path.open("xb"): pass
            self._path, self._active = path, True
        except Exception: self._path = None

    @property
    def path(self) -> Path | None: return self._path
    @property
    def active(self) -> bool: return self._active

    def emit(self, record: WarriorDiagnosticRecord) -> None:
        try:
            with self._lock:
                if not self._active or self._path is None: return
                if self._accepted >= MAX_EVENTS: self._active = False; return
                self._bytes = max(self._bytes, self._path.stat().st_size)
                payload = self._payload(record)
                data = (json.dumps(payload, ensure_ascii=True, allow_nan=False,
                    separators=(",", ":"), sort_keys=True) + "\n").encode()
                if len(data) > MAX_EVENT_BYTES or self._bytes + len(data) > MAX_SESSION_BYTES:
                    self._active = False; return
                with self._path.open("ab") as stream:
                    stream.write(data); stream.flush(); os.fsync(stream.fileno())
                self._accepted += 1; self._bytes += len(data)
                if self._accepted >= MAX_EVENTS or self._bytes >= MAX_SESSION_BYTES: self._active = False
        except Exception: self._active = False

    def emit_warrior(self, *, event: object, symbol: object = None, **fields: object) -> None:
        """Adapt integration callbacks to the closed record schema safely."""
        try:
            category = WarriorDiagnosticEvent(str(event))
            self.emit(WarriorDiagnosticRecord(
                event=category,
                symbol=symbol if isinstance(symbol, str) else None,
                session=(getattr(fields.get("session"), "value", fields.get("session"))
                         if isinstance(getattr(fields.get("session"), "value", fields.get("session")), str) else None),
                result=(fields.get("decision_result") or fields.get("admission_result") or fields.get("focus_action"))
                    if isinstance(fields.get("decision_result") or fields.get("admission_result") or fields.get("focus_action"), str) else None,
                reason=fields.get("admission_rejection") if isinstance(fields.get("admission_rejection"), str) else None,
                count=fields.get("focus_size") if isinstance(fields.get("focus_size"), int) else fields.get("queue_depth") if isinstance(fields.get("queue_depth"), int) else None,
                qualification=fields.get("qualification") if isinstance(fields.get("qualification"), str) else None,
                setup=fields.get("setup_category") if isinstance(fields.get("setup_category"), str) else None,
                admission=fields.get("admission_result") if isinstance(fields.get("admission_result"), str) else None,
                exception_class=fields.get("exception_class") if isinstance(fields.get("exception_class"), str) else None,
            ))
        except Exception:
            return None

    def close(self) -> None: self._active = False

    def _payload(self, record: WarriorDiagnosticRecord) -> dict[str, object]:
        if not isinstance(record, WarriorDiagnosticRecord): raise TypeError("diagnostic record is invalid")
        payload = {"schema_version": 1, "sequence": self._accepted + 1,
            "observed_at": self._clock().astimezone(UTC).isoformat(timespec="milliseconds"),
            "event": record.event.value}
        values = {"phase": _closed(record.phase, _PHASES), "symbol": _token(record.symbol),
            "session": _closed(record.session, _SESSIONS), "result": _closed(record.result, _RESULTS),
            "reason": _closed(record.reason, _ADMISSION_REASONS), "count": _integer(record.count, 0, 1_000_000),
            "latency_ms": _integer(record.latency_ms, 0, 86_400_000),
            "exception_class": _token(record.exception_class),
            "qualification": _closed(record.qualification, _QUALIFICATIONS), "setup": _closed(record.setup, _SETUPS),
            "admission": _closed(record.admission, _ADMISSIONS)}
        payload.update({k: v for k, v in values.items() if v is not None})
        return payload

def create_warrior_observability_sink(configuration: object) -> WarriorObservabilitySink:
    try:
        enabled = getattr(configuration, "observability_enabled",
                          getattr(configuration, "warrior_observability_enabled", False))
        if not bool(enabled): return NOOP_WARRIOR_OBSERVABILITY
        root = getattr(configuration, "observability_root",
                       getattr(configuration, "warrior_observability_root", None))
        session = getattr(configuration, "observability_session_id",
                          getattr(configuration, "warrior_observability_session_id", None))
        if root is None or session is None: return NOOP_WARRIOR_OBSERVABILITY
        sink = BoundedJsonlWarriorObservabilitySink(root, session)
        return sink if sink.active else NOOP_WARRIOR_OBSERVABILITY
    except Exception: return NOOP_WARRIOR_OBSERVABILITY

def _token(value: object) -> str | None:
    return value if isinstance(value, str) and _TOKEN.fullmatch(value) else None

def _closed(value: object, allowed: frozenset[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None

def _integer(value: object, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int): return None
    return min(maximum, max(minimum, value))

# Short aliases keep integration call sites independent of the on-disk format.
BoundedWarriorObservabilitySink = BoundedJsonlWarriorObservabilitySink
WarriorObservabilityRecord = WarriorDiagnosticRecord

__all__ = ["BoundedJsonlWarriorObservabilitySink", "BoundedWarriorObservabilitySink", "MAX_EVENTS", "MAX_EVENT_BYTES", "MAX_SESSION_BYTES",
    "NOOP_WARRIOR_OBSERVABILITY", "NoOpWarriorObservabilitySink", "WarriorDiagnosticEvent",
    "WarriorDiagnosticRecord", "WarriorObservabilityRecord", "WarriorObservabilitySink", "create_warrior_observability_sink", "safe_close", "safe_emit"]
