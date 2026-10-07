from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

from app.strategies.warrior_momentum.execution_quote import WebullExecutionQuoteSource
from app.strategies.warrior_momentum.execution_quote_provenance import (
    JsonlExecutionQuoteProvenanceSink,
    MAX_EVENTS,
    MAX_EVENT_BYTES,
    MAX_FILE_BYTES,
    NOOP_EXECUTION_QUOTE_PROVENANCE,
    create_execution_quote_provenance_sink_from_environment,
)
from app.webull.sdk_market_data import LazyOfficialDataClient
from tests.warrior_momentum.test_execution_quote_confirmation import NOW, evaluate


class _Response:
    status_code = 200

    def __init__(self, row: dict[str, object]) -> None:
        self.row = row

    def json(self) -> dict[str, object]:
        return {"data": [self.row]}


class _MarketData:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row
        self.calls = 0

    def get_snapshot(self, **_kwargs: object) -> _Response:
        self.calls += 1
        return _Response(self.row)


class _Client:
    def __init__(self, market_data: _MarketData) -> None:
        self.market_data = market_data


class _ThrowingSink:
    enabled = True

    def record(self, _event: object) -> None:
        raise RuntimeError("must remain contained")


def _source(row: dict[str, object], *, sink=NOOP_EXECUTION_QUOTE_PROVENANCE):
    market_data = _MarketData(row)
    source = WebullExecutionQuoteSource(
        LazyOfficialDataClient(lambda: _Client(market_data)),
        clock=lambda: NOW,
        provenance_clock=lambda: NOW,
        provenance_sink=sink,
    )
    return source, market_data


def _row(*, last_age: float = 0.560351, quote_age: float = 16.567351):
    return {
        "symbol": "CABO",
        "price": "13.20",
        "bid": "13.08",
        "ask": "13.21",
        "last_trade_time": int((NOW - timedelta(seconds=last_age)).timestamp() * 1000),
        "quote_time": int((NOW - timedelta(seconds=quote_age)).timestamp() * 1000),
        "bid_time": None,
        "provider_secret": "do-not-record",
    }


def _events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_default_configuration_is_noop_and_creates_no_file(tmp_path) -> None:
    sink = create_execution_quote_provenance_sink_from_environment({"TEMP": str(tmp_path)})
    assert sink is NOOP_EXECUTION_QUOTE_PROVENANCE
    source, market_data = _source(_row(), sink=sink)

    assert source("CABO") is not None
    assert market_data.calls == 1
    assert list(tmp_path.iterdir()) == []


def test_explicit_opt_in_creates_dedicated_artifact_only_on_record(tmp_path) -> None:
    sink = create_execution_quote_provenance_sink_from_environment({
        "ATLAS_EXECUTION_QUOTE_PROVENANCE_ENABLED": "true",
        "ATLAS_EXECUTION_QUOTE_PROVENANCE_DIRECTORY": str(tmp_path),
        "ATLAS_EXECUTION_QUOTE_PROVENANCE_SESSION_ID": "explicit-session",
    })
    assert sink.enabled is True
    assert not sink.path.exists()

    sink.record({"event_type": "EXECUTION_QUOTE_REQUESTED", "symbol": "CABO"})

    assert sink.path.name == (
        "atlas-execution-quote-provenance-explicit-session.jsonl"
    )
    assert sink.path.exists()


def test_raw_and_parsed_fields_are_allowlisted_and_correlated(tmp_path) -> None:
    sink = JsonlExecutionQuoteProvenanceSink(tmp_path, "capture")
    row = _row()
    row.update({"ask_time": 123, "update_time": 456, "timestamp": 789})
    source, market_data = _source(row, sink=sink)

    snapshot = source("CABO")
    assert snapshot is not None
    source.record_decision(
        evaluated_at=NOW,
        snapshot=snapshot,
        outcome="EXECUTION_QUOTE_REJECTED",
        rejection_reason="QUOTE_REJECTED_BID_STALE",
    )

    events = _events(sink.path)
    assert market_data.calls == 1
    assert [event["event_type"] for event in events] == [
        "EXECUTION_QUOTE_REQUESTED", "RAW_PROVIDER",
        "PARSED_EXECUTION_QUOTE", "FINAL_DECISION",
    ]
    assert len({event["request_id"] for event in events}) == 1
    raw = events[1]
    assert raw["raw_symbol"] == "CABO"
    assert raw["raw_price"] == "13.20"
    assert raw["raw_bid"] == "13.08"
    assert raw["raw_ask"] == "13.21"
    assert raw["raw_last_trade_time"] == str(row["last_trade_time"])
    assert raw["raw_quote_time"] == str(row["quote_time"])
    assert raw["has_bid_time"] is True
    assert raw["has_ask_time"] is True
    assert raw["has_update_time"] is True
    assert raw["has_timestamp"] is True
    assert raw["has_trade_time"] is False
    parsed = events[2]
    assert parsed["parsed_last"] == "13.20"
    assert parsed["parsed_bid"] == "13.08"
    assert parsed["parsed_ask"] == "13.21"
    assert parsed["parsed_bid_timestamp"] == parsed["parsed_ask_timestamp"]
    assert "provider_secret" not in sink.path.read_text(encoding="utf-8")


def test_cabo_stale_bid_rejection_is_unchanged_and_captured(tmp_path) -> None:
    sink = JsonlExecutionQuoteProvenanceSink(tmp_path, "cabo-stale")
    source, market_data = _source(_row(), sink=sink)

    candidate, signal = evaluate(tmp_path / "runtime", source)

    assert signal is None
    assert candidate.status.value == "AWAITING_EXECUTION_DATA"
    assert market_data.calls == 1
    decision = _events(sink.path)[-1]
    assert decision["final_outcome"] == "EXECUTION_QUOTE_REJECTED"
    assert decision["rejection_reason"] == "QUOTE_REJECTED_BID_STALE"
    assert Decimal("0.56") <= Decimal(
        decision["calculated_last_age_seconds"]
    ) <= Decimal("0.562")
    assert Decimal("16.567") <= Decimal(
        decision["calculated_bid_age_seconds"]
    ) <= Decimal("16.569")
    assert (
        decision["calculated_ask_age_seconds"]
        == decision["calculated_bid_age_seconds"]
    )


def test_fresh_quote_acceptance_is_unchanged_and_captured(tmp_path) -> None:
    sink = JsonlExecutionQuoteProvenanceSink(tmp_path, "fresh")
    source, market_data = _source(_row(last_age=1, quote_age=1), sink=sink)

    candidate, signal = evaluate(tmp_path / "runtime", source)

    assert candidate.status.value == "ENTRY_READY"
    assert signal is not None
    assert market_data.calls == 1
    decision = _events(sink.path)[-1]
    assert decision["final_outcome"] == "EXECUTION_QUOTE_ACCEPTED"
    assert "rejection_reason" not in decision


def test_malformed_snapshot_remains_unavailable(tmp_path) -> None:
    sink = JsonlExecutionQuoteProvenanceSink(tmp_path, "malformed")
    malformed = _row()
    malformed.pop("quote_time")
    source, market_data = _source(malformed, sink=sink)

    candidate, signal = evaluate(tmp_path / "runtime", source)

    assert signal is None
    assert candidate.status.value == "AWAITING_EXECUTION_DATA"
    assert market_data.calls == 1
    events = _events(sink.path)
    assert events[-2]["parse_status"] == "UNAVAILABLE"
    assert events[-1]["rejection_reason"] == "QUOTE_REJECTED_MISSING"


def test_observability_failure_cannot_change_snapshot_or_request_count(tmp_path) -> None:
    source, market_data = _source(_row(last_age=1, quote_age=1), sink=_ThrowingSink())

    candidate, signal = evaluate(tmp_path / "runtime", source)

    assert candidate.status.value == "ENTRY_READY"
    assert signal is not None
    assert market_data.calls == 1


def test_event_count_bound_stops_at_512_without_deleting_file(tmp_path) -> None:
    assert MAX_EVENTS == 512
    sink = JsonlExecutionQuoteProvenanceSink(tmp_path, "count-bound")
    for index in range(MAX_EVENTS + 1):
        sink.record({"event_type": "RAW_PROVIDER", "raw_price": index + 1})

    assert sink.events_written == MAX_EVENTS
    assert sink.stopped is True
    assert len(_events(sink.path)) == MAX_EVENTS


def test_event_and_file_size_bounds_stop_recording(tmp_path) -> None:
    assert MAX_EVENT_BYTES == 4096
    assert MAX_FILE_BYTES == 1024 * 1024
    event_limited = JsonlExecutionQuoteProvenanceSink(
        tmp_path, "event-bound", maximum_event_bytes=64,
    )
    event_limited.record({"event_type": "RAW_PROVIDER", "raw_price": "13.20"})
    assert event_limited.stopped is True
    assert event_limited.events_written == 0

    file_limited = JsonlExecutionQuoteProvenanceSink(
        tmp_path, "file-bound", maximum_file_bytes=1,
    )
    file_limited.record({"event_type": "RAW_PROVIDER", "raw_price": "13.20"})
    assert file_limited.stopped is True
    assert file_limited.events_written == 0


def test_exact_one_mibibyte_total_bound(tmp_path) -> None:
    sink = JsonlExecutionQuoteProvenanceSink(tmp_path, "exact-file-bound")
    large_event = {
        "event_type": "E" * 128,
        "request_id": "R" * 128,
        "symbol": "CABO",
        "raw_symbol": "CABO",
        "raw_row_type": "T" * 128,
        "raw_row_category": "C" * 128,
        "final_outcome": "O" * 128,
        "rejection_reason": "X" * 128,
        "parse_status": "P" * 128,
        "exception_class": "Q" * 128,
        **{
            field: "1" * 64 for field in (
                "raw_price", "raw_bid", "raw_ask", "parsed_last", "parsed_bid",
                "parsed_ask", "calculated_last_age_seconds",
                "calculated_bid_age_seconds", "calculated_ask_age_seconds",
            )
        },
        **{
            field: "1" * 64 for field in (
                "timestamp", "request_timestamp", "snapshot_return_timestamp",
                "evaluation_timestamp", "raw_last_trade_time", "raw_quote_time",
                "parsed_last_timestamp", "parsed_bid_timestamp",
                "parsed_ask_timestamp",
            )
        },
    }
    for _ in range(MAX_EVENTS):
        sink.record(large_event)
        if sink.stopped:
            break

    assert sink.stopped is True
    assert sink.events_written < MAX_EVENTS
    assert sink.bytes_written <= MAX_FILE_BYTES
    assert sink.path.stat().st_size == sink.bytes_written


def test_existing_completed_artifact_is_never_overwritten(tmp_path) -> None:
    first = JsonlExecutionQuoteProvenanceSink(tmp_path, "preserved")
    first.record({"event_type": "RAW_PROVIDER", "raw_price": "13.20"})
    original = first.path.read_bytes()

    second = JsonlExecutionQuoteProvenanceSink(tmp_path, "preserved")
    second.record({"event_type": "RAW_PROVIDER", "raw_price": "99.99"})

    assert second.stopped is True
    assert first.path.read_bytes() == original


def test_sanitization_excludes_unknown_payloads_secrets_and_messages(tmp_path) -> None:
    sink = JsonlExecutionQuoteProvenanceSink(tmp_path, "sanitize")
    sink.record({
        "event_type": "RAW_PROVIDER_UNAVAILABLE",
        "symbol": "CABO",
        "raw_price": "https://provider.invalid/path?token=secret",
        "headers": {"Authorization": "Bearer secret"},
        "cookies": "secret",
        "account_id": "123",
        "response_body": {"token": "secret"},
        "exception_class": "TimeoutError",
        "exception_message": "token=secret",
        "unknown": object(),
    })

    text = sink.path.read_text(encoding="utf-8")
    event = json.loads(text)
    assert event["exception_class"] == "TimeoutError"
    assert "secret" not in text
    assert "headers" not in event
    assert "cookies" not in event
    assert "account_id" not in event
    assert "response_body" not in event
    assert "exception_message" not in event
    assert "raw_price" not in event
