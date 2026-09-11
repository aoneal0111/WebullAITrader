from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.performance_diagnostics import PerformanceDiagnostics
from app.webull.configuration import ReconnectPolicy
from app.webull.errors import NetworkError
from app.webull.websocket_client import WebullWebSocketClient


class _Backend:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.generation_metrics = {"generation": 3}
        self.session_identity_hash = "session-hash"
        self.connects = 0
        self.subscriptions = 0

    def receive(self):
        if self.failures:
            self.failures -= 1
            raise OSError("authorization=token-secret cookie=session-cookie")
        return None

    def connect(self) -> None:
        self.connects += 1

    def subscribe(self, channels) -> None:
        self.subscriptions += 1


def test_receive_failure_and_successful_reconnect_are_durable(monkeypatch) -> None:
    import app.webull.websocket_client as websocket_client

    diagnostics = PerformanceDiagnostics()
    monkeypatch.setattr(websocket_client, "performance_diagnostics", diagnostics)
    backend = _Backend(failures=1)
    client = WebullWebSocketClient(
        backend,
        lambda message: None,
        ReconnectPolicy(maximum_attempts=1),
        lambda seconds: None,
        type("Logger", (), {"log": lambda *args, **kwargs: None})(),
    )

    assert client.receive() is None
    metrics = diagnostics.stream_metrics()
    assert metrics["receive_failures_total"] == 1
    assert metrics["reconnect_attempts_total"] == 1
    assert metrics["reconnect_successes"] == 1
    assert metrics["reconnect_failures"] == 0
    assert metrics["latest_lifecycle_state"] == "RECONNECTED"
    assert metrics["current_stream_generation"] == 3
    assert metrics["failure_samples"][0]["exception_class"] == "OSError"
    assert "token-secret" not in json.dumps(metrics)
    assert "session-cookie" not in json.dumps(metrics)


def test_reconnect_exhaustion_retains_bounded_original_and_terminal_metadata(
    monkeypatch,
) -> None:
    import app.webull.websocket_client as websocket_client

    diagnostics = PerformanceDiagnostics()
    monkeypatch.setattr(websocket_client, "performance_diagnostics", diagnostics)
    backend = _Backend(failures=100)
    client = WebullWebSocketClient(
        backend,
        lambda message: None,
        ReconnectPolicy(maximum_attempts=2),
        lambda seconds: None,
        type("Logger", (), {"log": lambda *args, **kwargs: None})(),
    )

    with pytest.raises(NetworkError, match="reconnect exhausted"):
        client.receive()

    metrics = diagnostics.stream_metrics()
    assert metrics["terminal_stream_failures"] == 1
    assert metrics["reconnect_exhausted"] == 1
    assert metrics["reconnect_attempts_total"] == 2
    assert metrics["latest_lifecycle_state"] == "TERMINAL_FAILED"
    assert len(metrics["failure_samples"]) == 3
    assert metrics["failure_samples"][-1]["terminal"] is True
    assert metrics["failure_samples"][-1]["exception_class"] == "NetworkError"
    assert all("token-secret" not in json.dumps(item) for item in metrics["failure_samples"])


def test_stream_boundaries_and_duration_order_are_bounded() -> None:
    diagnostics = PerformanceDiagnostics()
    diagnostics.record_stream_raw_callback(generation=4)
    diagnostics.record_stream_normalized_event()
    terminal_at = datetime(2026, 9, 11, 13, 0, 0, tzinfo=UTC)
    diagnostics.record_stream_lifecycle("terminal_failure", timestamp=terminal_at)
    diagnostics.record_stream_boundary(
        "callback_ingestion_halted", timestamp=datetime(2026, 9, 11, 13, 0, 1, tzinfo=UTC)
    )
    diagnostics.record_stream_boundary(
        "consumer_stopped", timestamp=datetime(2026, 9, 11, 13, 0, 2, tzinfo=UTC)
    )
    diagnostics.record_stream_boundary(
        "transport_disconnected", timestamp=datetime(2026, 9, 11, 13, 0, 3, tzinfo=UTC)
    )
    diagnostics.record_stream_boundary(
        "broker_disconnect_started", timestamp=datetime(2026, 9, 11, 13, 0, 4, tzinfo=UTC)
    )
    diagnostics.record_stream_boundary(
        "broker_disconnect_completed", timestamp=datetime(2026, 9, 11, 13, 0, 5, tzinfo=UTC)
    )
    diagnostics.record_startup_stage("runtime_started")
    diagnostics.record_startup_stage("broker_connect_started")

    metrics = diagnostics.stream_metrics()
    startup = diagnostics.startup_metrics()
    assert metrics["last_good_raw_callback_at"] is not None
    assert metrics["last_normalized_event_at"] is not None
    assert metrics["callback_ingestion_halted_at"] is not None
    assert metrics["consumer_stopped_at"] is not None
    assert metrics["transport_disconnected_at"] is not None
    assert metrics["broker_disconnect_started_at"] is not None
    assert metrics["broker_disconnect_completed_at"] is not None
    assert metrics["terminal_failure_at"] < metrics["consumer_stopped_at"]
    assert metrics["consumer_stopped_at"] < metrics["transport_disconnected_at"]
    assert startup["runtime_start_to_broker_connect_ms"] >= 0
    assert len(metrics["failure_samples"]) <= 16


def test_stream_failure_artifact_is_serialized_and_samples_remain_bounded(
    tmp_path,
) -> None:
    diagnostics = PerformanceDiagnostics()
    artifact = tmp_path / "stream-diagnostics.json"
    diagnostics.start_run(artifact_path=artifact, flush_seconds=60.0)
    for index in range(32):
        diagnostics.record_stream_lifecycle(
            "reconnecting",
            error=RuntimeError(f"secret={index}"),
            attempt=index,
            maximum_attempts=32,
            generation=2,
            session_id_hash="safe-hash",
        )
    diagnostics.finish_run()

    payload = json.loads(artifact.read_text(encoding="utf-8"))
    stream = payload["stream"]
    assert stream["receive_failures_total"] == 32
    assert len(stream["failure_samples"]) == 16
    assert all(item["session_id_hash"] == "safe-hash" for item in stream["failure_samples"])
    assert "secret=0" not in json.dumps(stream)
