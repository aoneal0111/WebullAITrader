from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.webull.websocket_client import OfficialSdkStreamBackend


class _Client:
    def __init__(self) -> None:
        self._client_id = "offline"
        self._quotes_session_id = "offline"
        self.api_client = object()
        self.on_quotes_message = None
        self.on_connect_success = None
        self.on_disconnect = None


def test_raw_fifo_residence_is_measured_under_a_replay_burst() -> None:
    clock = {"value": datetime(2026, 9, 23, tzinfo=UTC)}
    client = _Client()
    stream = OfficialSdkStreamBackend(
        client,
        clock=lambda: clock["value"],
        receive_timeout_seconds=0,
    )

    for _ in range(3_000):
        client.on_quotes_message(client, "offline", object())

    # A deterministic consumer slower than the replay producer demonstrates
    # the unchanged raw FIFO boundary; no event is discarded.
    for _ in range(3_000):
        assert stream.receive_nowait() is not None
        clock["value"] += timedelta(milliseconds=5)

    metrics = stream.memory_metrics()
    latency = stream.latency_metrics()
    assert metrics["high_water_depth"] == 3_000
    assert metrics["messages_enqueued"] == metrics["messages_dequeued"] == 3_000
    assert latency["dequeue_residence_p99_ms"] > 5_000.0
    assert latency["dequeue_residence_max_ms"] == 14_995.0
