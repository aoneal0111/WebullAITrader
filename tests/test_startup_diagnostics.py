from __future__ import annotations

import json

from app.memory_observability.runtime import MemoryObservability
from app.performance_diagnostics import PerformanceDiagnostics
from app.webull.websocket_client import WebullWebSocketClient


def test_startup_stage_is_first_occurrence_and_durations_are_bounded() -> None:
    diagnostics = PerformanceDiagnostics()

    diagnostics.record_startup_stage("transport_connected")
    diagnostics.record_startup_stage("transport_connected")
    diagnostics.increment_startup_counter("decode_attempts", 3)

    metrics = diagnostics.startup_metrics()

    assert metrics["transport_connected_at"] is not None
    assert metrics["registration_ready_at"] is None
    assert metrics["transport_to_registration_ms"] is None
    assert metrics["decode_attempts"] == 3
    assert len(metrics) < 64


def test_startup_counters_and_reference_symbol_are_scalar_only() -> None:
    diagnostics = PerformanceDiagnostics()

    diagnostics.increment_startup_counter("reference_warmup_symbols_total", 62)
    diagnostics.increment_startup_counter("reference_warmup_symbols_completed", 61)
    diagnostics.increment_startup_counter("reference_warmup_symbols_accepted", 60)
    diagnostics.increment_startup_counter("reference_warmup_symbols_rejected")
    diagnostics.set_startup_reference_symbol(" aapl ")

    metrics = diagnostics.startup_metrics()

    assert metrics["reference_warmup_symbols_total"] == 62
    assert metrics["reference_warmup_symbols_completed"] == 61
    assert metrics["reference_warmup_symbols_accepted"] == 60
    assert metrics["reference_warmup_symbols_rejected"] == 1
    assert metrics["reference_warmup_current_symbol"] == "AAPL"
    assert not any(isinstance(value, (list, tuple, dict)) for value in metrics.values())


def test_websocket_client_surfaces_existing_backend_queue_metrics() -> None:
    class Backend:
        def memory_metrics(self):
            return {
                "current_depth": 4,
                "high_water_depth": 9,
                "messages_enqueued": 12,
                "messages_dequeued": 8,
            }

    client = WebullWebSocketClient(
        Backend(),
        lambda message: None,
        object(),
        lambda seconds: None,
        object(),
    )

    assert client.memory_metrics() == {
        "current_depth": 4,
        "high_water_depth": 9,
        "messages_enqueued": 12,
        "messages_dequeued": 8,
    }


def test_startup_metrics_reach_jsonl_as_scalar_values(tmp_path) -> None:
    diagnostics = PerformanceDiagnostics()
    diagnostics.record_startup_stage("transport_connected")
    diagnostics.increment_startup_counter("raw_callbacks_received")

    path = tmp_path / "memory.jsonl"
    observer = MemoryObservability(
        {
            "startup": diagnostics.startup_metrics,
            "websocket_callback_queue": lambda: {
                "current_depth": 2,
                "high_water_depth": 7,
                "messages_enqueued": 3,
                "messages_dequeued": 1,
            },
        },
        enabled=True,
        path=path,
    )

    snapshot = observer.sample()
    assert snapshot is not None
    payload = snapshot.to_dict()
    metrics = payload["metrics"]

    assert metrics["startup_transport_connected_at"] is not None
    assert metrics["startup_raw_callbacks_received"] == 1
    assert metrics["websocket_callback_queue_current_depth"] == 2
    assert metrics["websocket_callback_queue_high_water_depth"] == 7
    assert metrics["websocket_callback_queue_messages_enqueued"] == 3
    assert metrics["websocket_callback_queue_messages_dequeued"] == 1
    json.dumps(payload)
