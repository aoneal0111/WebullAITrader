from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.market_data.models import (
    MarketEvent,
    MarketEventType,
    TradePayload,
)
from app.performance_diagnostics import PerformanceDiagnostics


NOW = datetime(2026, 8, 31, 17, 33, 5, tzinfo=UTC)


def event() -> MarketEvent:
    return MarketEvent(
        42,
        NOW - timedelta(seconds=7),
        "XYZ",
        "WEBULL",
        MarketEventType.TRADE,
        TradePayload(Decimal("1.25"), Decimal("100"), "trade-42"),
        received_timestamp=NOW - timedelta(seconds=6),
        dequeued_timestamp=NOW - timedelta(seconds=5, milliseconds=500),
    )


def test_queue_threshold_crossings_and_recoveries_are_sparse_and_complete() -> None:
    diagnostics = PerformanceDiagnostics()
    records = []
    diagnostics.set_diagnostic_sink(lambda kind, payload: records.append((kind, payload)))
    for depth in (1, 99, 100, 499, 500, 999, 1000, 1499, 1500, 1707):
        diagnostics.record_market_event_callback(depth)
    for depth in (1600, 1499, 999, 499, 99, 0):
        diagnostics.set_callback_queue_depth(depth)
    assert [(item[1]["threshold"], item[1]["direction"]) for item in records] == [
        (100, "CROSSED_UP"),
        (500, "CROSSED_UP"),
        (1000, "CROSSED_UP"),
        (1500, "CROSSED_UP"),
        (1500, "RECOVERED_BELOW"),
        (1000, "RECOVERED_BELOW"),
        (500, "RECOVERED_BELOW"),
        (100, "RECOVERED_BELOW"),
    ]
    assert all(kind == "callback_queue_threshold" for kind, _ in records)
    assert records[3][1]["callback_queue_high_water"] == 1500
    assert records[4][1]["callback_queue_high_water"] == 1707


def test_abnormal_event_record_contains_reconstructable_stage_and_safety_data() -> None:
    diagnostics = PerformanceDiagnostics()
    records = []
    diagnostics.set_diagnostic_sink(lambda kind, payload: records.append((kind, payload)))
    value = event()
    diagnostics.record_market_event_callback(1200)
    diagnostics.set_callback_queue_depth(1100)
    diagnostics.begin_latency_trace(value, NOW - timedelta(seconds=5))
    diagnostics.mark_latency_trace_timestamp(
        "scanner_ended_at", NOW - timedelta(seconds=4)
    )
    diagnostics.mark_latency_trace_timestamp(
        "experiment_enqueue_started_at", NOW - timedelta(seconds=4, milliseconds=50)
    )
    diagnostics.mark_latency_trace_timestamp(
        "experiment_enqueue_ended_at", NOW - timedelta(seconds=4)
    )
    diagnostics.mark_latency_trace_timestamp(
        "observer_started_at", NOW - timedelta(seconds=4)
    )
    diagnostics.mark_latency_trace_stage("completed_bar_flush_duration_ms", 42.5)
    diagnostics.mark_latency_trace_stage("report_refresh_request_duration_ms", 0.03)
    diagnostics.record_execution_safety(
        processing_delayed=True,
        entry_authorized=False,
        execution_quote_requested=False,
        paper_order_created=False,
    )
    diagnostics.finish_latency_trace(NOW)
    abnormal = [payload for kind, payload in records if kind == "market_latency_abnormal"]
    assert len(abnormal) == 1
    payload = abnormal[0]
    assert payload["sequence"] == 42
    assert payload["symbol"] == "XYZ"
    assert payload["event_type"] == "TRADE"
    assert payload["callback_received_at"] == value.received_timestamp.isoformat()
    assert payload["dequeued_at"] == value.dequeued_timestamp.isoformat()
    assert payload["callback_queue_depth_at_dequeue"] == 1100
    assert payload["callback_queue_high_water"] == 1200
    assert payload["total_processing_age_ms"] == 6000.0
    assert payload["source_delivery_age_ms"] == 7000.0
    assert payload["completed_bar_flush_duration_ms"] == 42.5
    assert payload["report_refresh_request_duration_ms"] == 0.03
    assert payload["execution_safety"] == {
        "processing_delayed": True,
        "entry_authorized": False,
        "execution_quote_requested": False,
        "paper_order_created": False,
    }


def test_normal_event_does_not_emit_per_event_diagnostic() -> None:
    diagnostics = PerformanceDiagnostics()
    records = []
    diagnostics.set_diagnostic_sink(lambda kind, payload: records.append((kind, payload)))
    value = event()
    value = MarketEvent(
        value.sequence, NOW, value.symbol, value.source, value.event_type,
        value.payload, received_timestamp=NOW, dequeued_timestamp=NOW,
    )
    diagnostics.begin_latency_trace(value, NOW)
    diagnostics.record_execution_safety(
        processing_delayed=False,
        entry_authorized=False,
        execution_quote_requested=False,
        paper_order_created=False,
    )
    diagnostics.finish_latency_trace(NOW + timedelta(milliseconds=2))
    assert records == []
