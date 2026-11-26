"""Recordings contain observations only, even when names resemble fills."""
import json
from pathlib import Path

from app.strategy_research.closed_recording_evidence import inventory


def prepare(tmp_path, *, closed=True, writer_alive=False):
    trial = tmp_path / "trial"
    trial.mkdir()
    session = tmp_path / "session"
    session.mkdir()
    (session / "journal").mkdir()
    pointer = trial / "recording-session.json"
    pointer.write_text(json.dumps({"session": str(session)}))
    (session / "research-manifest.json").write_text('{"started_at":"2026-11-10T15:00:00Z"}')
    (session / "research-summary.json").write_text(
        json.dumps({"journal": {"closed": closed, "writer_alive": writer_alive}}))
    return pointer, session / "journal"


def test_missing_pointer_never_creates_files(tmp_path):
    path = tmp_path / "recording-session.json"
    result = inventory(path)
    assert result["status"] == "RECORDING_METADATA_INVALID_OR_UNAVAILABLE"
    assert not path.exists()
    assert result["authoritative_order_ledger_proven"] is False


def test_closed_synthetic_events_are_not_confirmed_fills(tmp_path):
    pointer, journal = prepare(tmp_path)
    (journal / "events-00001.jsonl").write_text(
        '{"event_type":"ORDER_FILLED","symbol":"SECRET","token":"SECRET_TOKEN"}\n'
        '{"event_type":"ORDER_SUBMITTED","account_id":"PRIVATE"}\n'
        '{"event_type":"PRICE_UPDATE","symbol":"PRIVATE"}\n')
    result = inventory(pointer)
    assert result["status"] == "CANDIDATE_ORDER_EVENTS_UNVERIFIED"
    assert result["candidate_event_lines"] == 2
    assert result["unknown_event_lines"] == 1
    assert result["event_type_counts"] == {"ORDER_FILLED": 1, "ORDER_SUBMITTED": 1}
    assert result["fills_reconciled"] is False
    assert result["protective_exits_verified"] is False
    assert "SECRET" not in json.dumps(result)
    assert "PRIVATE" not in json.dumps(result)


def test_open_recording_refused_before_event_read(tmp_path):
    pointer, journal = prepare(tmp_path, closed=False, writer_alive=True)
    (journal / "events-00001.jsonl").write_text('{"event_type":"ORDER_FILLED"}\n')
    result = inventory(pointer)
    assert result["status"] == "RECORDING_CLOSURE_UNPROVEN"
    assert result["line_count"] == 0
    assert result["candidate_event_lines"] == 0


def test_missing_journal_not_conflated_with_missing_ledger(tmp_path):
    pointer, journal = prepare(tmp_path)
    result = inventory(pointer)
    assert result["status"] == "NO_EVENT_FILES"
    assert result["closed_recording_verified"] is True
    assert result["authoritative_order_ledger_proven"] is False


def test_bounded_failure_does_not_report_partial_events_as_complete(tmp_path):
    pointer, journal = prepare(tmp_path)
    (journal / "events-00001.jsonl").write_text(
        '{"event_type":"ORDER_FILLED"}\n' + "X" * (16 * 1024 + 1) + "\n")
    result = inventory(pointer)
    assert result["status"] == "JOURNAL_BOUNDS_EXCEEDED"
    assert not result["fills_reconciled"]


def test_unsupported_nested_types_never_inferred(tmp_path):
    pointer, journal = prepare(tmp_path)
    (journal / "events-00001.jsonl").write_text(
        '{"payload":{"event_type":"ORDER_FILLED"}}\n'
        '{"event_type":"ORDER_FILLED; SECRET"}\n'
        'not-json\n')
    result = inventory(pointer)
    assert result["status"] == "NO_RECOGNIZED_ORDER_EVENTS"
    assert result["candidate_event_lines"] == 0
    assert result["invalid_lines"] == 1
