from pathlib import Path
from datetime import UTC, datetime
from app.trade_intelligence.service import TradeIntelligenceService, _Work

from app.strategies.warrior_momentum.observability import (
    BoundedJsonlWarriorObservabilitySink, WarriorDiagnosticEvent,
    WarriorDiagnosticRecord,
    safe_emit,
)
from app.strategies.warrior_momentum.desktop_sidecar import _safe_warrior_observe
from app.trade_intelligence.runtime import _safe_warrior_observe as safe_runtime_observe
import json
import pytest
import app.strategies.warrior_momentum.observability as obs


def test_explicit_sink_emits_sanitized_event(tmp_path: Path):
    sink = BoundedJsonlWarriorObservabilitySink(tmp_path, "session-1")
    sink.emit_warrior(event="SCANNER_SEEN", symbol="DAIC", qualification="QUALIFIED")
    text = sink.path.read_text(encoding="utf-8")
    assert '"event":"SCANNER_SEEN"' in text
    assert '"symbol":"DAIC"' in text
    assert "secret" not in text


def test_event_bound_is_bounded(tmp_path: Path):
    sink = BoundedJsonlWarriorObservabilitySink(tmp_path, "session-2")
    for _ in range(2100):
        sink.emit_warrior(event="SCANNER_SEEN", symbol="DAIC")
    assert len(sink.path.read_text(encoding="utf-8").splitlines()) <= 2048


def test_invalid_event_is_noop(tmp_path: Path):
    sink = BoundedJsonlWarriorObservabilitySink(tmp_path, "session-3")
    sink.emit_warrior(event="NOT_A_REAL_EVENT", symbol="DAIC")
    assert sink.path.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("field", (
    "phase", "session", "result", "reason", "qualification", "setup", "admission",
))
def test_semantic_fields_are_field_specific_closed_values(tmp_path: Path, field: str):
    sink = BoundedJsonlWarriorObservabilitySink(tmp_path, f"closed-{field}")
    record = WarriorDiagnosticRecord(
        event=WarriorDiagnosticEvent.SCANNER_SEEN,
        symbol="DAIC",
        **{field: "ARBITRARY_NOT_IN_TAXONOMY"},
    )
    sink.emit(record)
    payload = json.loads(sink.path.read_text(encoding="utf-8"))
    assert field not in payload


def test_session_category_is_emitted_when_supplied(tmp_path: Path):
    sink = BoundedJsonlWarriorObservabilitySink(tmp_path, "session-category")
    sink.emit_warrior(event="SCANNER_SEEN", symbol="DAIC", session="REGULAR")
    payload = json.loads(sink.path.read_text(encoding="utf-8"))
    assert payload["session"] == "REGULAR"


class _ThrowingSink:
    def __init__(self):
        self.calls = 0

    def emit(self, _record):
        self.calls += 1
        raise RuntimeError("must remain contained")

    def emit_warrior(self, **_fields):
        self.calls += 1
        raise RuntimeError("must remain contained")


def test_safe_emit_and_all_integration_wrappers_contain_sink_failures():
    sink = _ThrowingSink()
    record = WarriorDiagnosticRecord(event=WarriorDiagnosticEvent.SCANNER_SEEN, symbol="DAIC")
    assert safe_emit(sink, record) is None
    assert _safe_warrior_observe(sink, "SCANNER_SEEN", "DAIC") is None
    assert safe_runtime_observe(sink, "SCANNER_SEEN", "DAIC") is None
    assert sink.calls == 3


@pytest.mark.parametrize("action", ("FIRST_INSERT", "REPLACE_EXISTING", "TOP_N_OMITTED", "DROPPED_FROM_PRIOR_SNAPSHOT"))
def test_focus_actions_are_preserved_as_closed_values(tmp_path: Path, action: str):
    sink = BoundedJsonlWarriorObservabilitySink(tmp_path, f"focus-{action.lower()}")
    sink.emit_warrior(event="FOCUS_PROJECTION_OMIT", symbol="DAIC", focus_action=action)
    payload = json.loads(sink.path.read_text(encoding="utf-8"))
    # The current compact on-disk schema carries focus action in result.
    assert payload.get("result") == action


def test_diagnostic_record_rejects_sensitive_and_unbounded_fields(tmp_path: Path):
    sink = BoundedJsonlWarriorObservabilitySink(tmp_path, "sanitize-fields")
    sink.emit(WarriorDiagnosticRecord(
        event=WarriorDiagnosticEvent.SCANNER_SEEN,
        symbol="DAIC",
        exception_class="ValueError: secret body https://example.test?q=token",
    ))
    payload = json.loads(sink.path.read_text(encoding="utf-8"))
    assert "exception_class" not in payload
    text = sink.path.read_text(encoding="utf-8")
    assert "secret body" not in text
    assert "https://" not in text


def test_noop_sink_does_not_create_diagnostic_files(tmp_path: Path):
    from app.strategies.warrior_momentum.observability import NOOP_WARRIOR_OBSERVABILITY

    NOOP_WARRIOR_OBSERVABILITY.emit_warrior(event="SCANNER_SEEN", symbol="DAIC")
    assert tuple(tmp_path.iterdir()) == ()


def test_invalid_focus_action_is_not_serialized(tmp_path: Path):
    sink = BoundedJsonlWarriorObservabilitySink(tmp_path, "invalid-focus")
    sink.emit_warrior(event="FOCUS_PROJECTION_OMIT", symbol="DAIC", focus_action="NOT_ALLOWED")
    payload = json.loads(sink.path.read_text(encoding="utf-8"))
    assert "result" not in payload


def test_symbol_and_exception_are_bounded_and_sanitized(tmp_path: Path):
    sink = BoundedJsonlWarriorObservabilitySink(tmp_path, "bounded-values")
    sink.emit(WarriorDiagnosticRecord(
        event=WarriorDiagnosticEvent.SCANNER_SEEN,
        symbol="a" * 200,
        exception_class="ValueError",
    ))
    payload = json.loads(sink.path.read_text(encoding="utf-8"))
    assert "symbol" not in payload
    assert payload["exception_class"] == "ValueError"


@pytest.mark.parametrize("field, value", [
    ("session", "REGULAR"), ("qualification", "QUALIFIED"),
    ("setup", "BULL_FLAG"), ("admission", "ADMITTED"),
    ("reason", "QUEUE_FULL"), ("phase", "WARRIOR"),
    ("result", "ENTRY_READY"),
])
def test_all_semantic_production_values_are_serialized(tmp_path: Path, field: str, value: str):
    sink = BoundedJsonlWarriorObservabilitySink(tmp_path, "valid-" + field)
    sink.emit(WarriorDiagnosticRecord(event=WarriorDiagnosticEvent.SCANNER_SEEN, **{field: value}))
    assert value in sink.path.read_text(encoding="utf-8")


@pytest.mark.parametrize("field", ("session", "qualification", "setup", "admission", "reason", "phase", "result"))
def test_arbitrary_semantic_values_are_rejected(tmp_path: Path, field: str):
    sink = BoundedJsonlWarriorObservabilitySink(tmp_path, "invalid-" + field)
    sink.emit(WarriorDiagnosticRecord(event=WarriorDiagnosticEvent.SCANNER_SEEN, **{field: "RAW_UNTRUSTED_VALUE"}))
    assert "RAW_UNTRUSTED_VALUE" not in sink.path.read_text(encoding="utf-8")


def test_exclusive_session_creation_preserves_existing_file(tmp_path: Path):
    first = BoundedJsonlWarriorObservabilitySink(tmp_path, "same-session")
    first.emit(WarriorDiagnosticRecord(event=WarriorDiagnosticEvent.SCANNER_SEEN))
    second = BoundedJsonlWarriorObservabilitySink(tmp_path, "same-session")
    assert not second.active
    assert len(first.path.read_text(encoding="utf-8").splitlines()) == 1


def test_close_is_safe_and_stops_accepting(tmp_path: Path):
    sink = BoundedJsonlWarriorObservabilitySink(tmp_path, "close-session")
    sink.close()
    sink.emit(WarriorDiagnosticRecord(event=WarriorDiagnosticEvent.SCANNER_SEEN))
    assert sink.path.read_text(encoding="utf-8") == ""


class _ThrowingDiagnostic:
    def emit_warrior(self, **_kwargs):
        raise RuntimeError("diagnostic")


def test_service_queue_outcome_is_unchanged_with_throwing_sink(tmp_path: Path):
    baseline = TradeIntelligenceService(tmp_path / "base.sqlite3", capacity=1)
    throwing = TradeIntelligenceService(tmp_path / "throw.sqlite3", capacity=1, observability=_ThrowingDiagnostic())
    work = _Work("one", "TEST", "{}", datetime.now(UTC))
    try:
        assert baseline._submit(work) is True
        assert baseline._submit(_Work("two", "TEST", "{}", datetime.now(UTC))) is False
        assert throwing._submit(work) is True
        assert throwing._submit(_Work("two", "TEST", "{}", datetime.now(UTC))) is False
    finally:
        baseline.close(timeout_seconds=1)
        throwing.close(timeout_seconds=1)


def test_exclusive_session_creation_does_not_overwrite_completed_session(tmp_path: Path):
    first = obs.BoundedJsonlWarriorObservabilitySink(tmp_path, "completed")
    first.emit_warrior(event="SCANNER_SEEN", symbol="DAIC")
    original = first.path.read_bytes()
    second = obs.BoundedJsonlWarriorObservabilitySink(tmp_path, "completed")
    assert not second.active
    assert second.path is None
    assert first.path.read_bytes() == original


def test_session_size_bound_stops_accepting_without_affecting_caller(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(obs, "MAX_SESSION_BYTES", 1)
    sink = obs.BoundedJsonlWarriorObservabilitySink(tmp_path, "small-session")
    sink.emit_warrior(event="SCANNER_SEEN", symbol="DAIC")
    assert sink.active is False
    assert sink.path.read_bytes() == b""
    sink.emit_warrior(event="SCANNER_SEEN", symbol="DAIC")


def test_oversized_serialized_event_is_rejected_without_error(tmp_path: Path, monkeypatch):
    sink = obs.BoundedJsonlWarriorObservabilitySink(tmp_path, "large-event")
    monkeypatch.setattr(sink, "_payload", lambda _record: {"raw": "x" * (obs.MAX_EVENT_BYTES + 1)})
    sink.emit_warrior(event="SCANNER_SEEN", symbol="DAIC")
    assert sink.active is False
    assert sink.path.read_bytes() == b""


@pytest.mark.parametrize("failure", ("mkdir", "open"))
def test_session_initialization_failures_degrade_to_noop(tmp_path: Path, monkeypatch, failure: str):
    if failure == "mkdir":
        monkeypatch.setattr(Path, "mkdir", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("mkdir")))
    else:
        original_open = Path.open
        def fail_open(self, *args, **kwargs):
            raise OSError("open")
        monkeypatch.setattr(Path, "open", fail_open)
    sink = obs.BoundedJsonlWarriorObservabilitySink(tmp_path, f"init-{failure}")
    assert sink.active is False
    sink.emit_warrior(event="SCANNER_SEEN", symbol="DAIC")


@pytest.mark.parametrize("operation", ("stat", "open", "flush", "fsync", "serialize"))
def test_storage_failures_are_contained_after_session_creation(tmp_path: Path, monkeypatch, operation: str):
    sink = obs.BoundedJsonlWarriorObservabilitySink(tmp_path, f"failure-{operation}")
    if operation == "stat":
        monkeypatch.setattr(Path, "stat", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("stat")))
    elif operation == "open":
        monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("write")))
    elif operation == "flush":
        class FlushFailure:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def write(self, _data): return None
            def flush(self): raise OSError("flush")
            def fileno(self): return 1
        monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: FlushFailure())
    elif operation == "fsync":
        monkeypatch.setattr(obs.os, "fsync", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("fsync")))
    else:
        monkeypatch.setattr(sink, "_payload", lambda _record: (_ for _ in ()).throw(TypeError("serialization")))
    sink.emit_warrior(event="SCANNER_SEEN", symbol="DAIC")
    assert sink.active is False


@pytest.mark.parametrize(
    ("field", "allowed"),
    (("session", obs._SESSIONS), ("qualification", obs._QUALIFICATIONS),
     ("setup", obs._SETUPS), ("admission", obs._ADMISSIONS),
     ("reason", obs._ADMISSION_REASONS), ("phase", obs._PHASES),
     ("result", obs._RESULTS), ("focus_action", obs._FOCUS_ACTIONS)),
)
def test_every_closed_semantic_value_is_serializable(tmp_path: Path, field: str, allowed):
    for index, value in enumerate(allowed):
        sink = obs.BoundedJsonlWarriorObservabilitySink(tmp_path, f"value-{field}-{index}")
        kwargs = {field: value}
        if field == "focus_action":
            kwargs = {"result": value}
        sink.emit(obs.WarriorDiagnosticRecord(event=obs.WarriorDiagnosticEvent.SCANNER_SEEN,
                                               symbol="DAIC", **kwargs))
        payload = json.loads(sink.path.read_text(encoding="utf-8"))
        assert payload.get(field if field != "focus_action" else "result") == value


@pytest.mark.parametrize("field", ("session", "qualification", "setup", "admission", "reason", "phase", "result"))
def test_arbitrary_semantic_values_are_not_serialized(tmp_path: Path, field: str):
    sink = obs.BoundedJsonlWarriorObservabilitySink(tmp_path, f"invalid-{field}")
    sink.emit(obs.WarriorDiagnosticRecord(event=obs.WarriorDiagnosticEvent.SCANNER_SEEN,
                                           symbol="DAIC", **{field: "not-a-real-category"}))
    payload = json.loads(sink.path.read_text(encoding="utf-8"))
    assert field not in payload


def test_exception_class_is_only_sanitized_token(tmp_path: Path):
    sink = obs.BoundedJsonlWarriorObservabilitySink(tmp_path, "exception-class")
    sink.emit(obs.WarriorDiagnosticRecord(event=obs.WarriorDiagnosticEvent.SCANNER_SEEN,
                                           symbol="DAIC", exception_class="ValueError"))
    payload = json.loads(sink.path.read_text(encoding="utf-8"))
    assert payload["exception_class"] == "ValueError"
    sink = obs.BoundedJsonlWarriorObservabilitySink(tmp_path, "exception-message")
    sink.emit(obs.WarriorDiagnosticRecord(event=obs.WarriorDiagnosticEvent.SCANNER_SEEN,
                                           symbol="DAIC", exception_class="ValueError: secret"))
    assert "exception_class" not in json.loads(sink.path.read_text(encoding="utf-8"))
