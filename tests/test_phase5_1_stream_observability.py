from __future__ import annotations

from datetime import UTC, datetime

from app.performance_diagnostics import PerformanceDiagnostics, subscription_fingerprint
from app.webull.websocket_client import OfficialSdkStreamBackend


class _Sdk:
    def __init__(self) -> None:
        self.api_client = object()
        self.on_quotes_message = None
        self.on_connect_success = None
        self.on_disconnect = None
        self.calls: list[tuple[str, object]] = []

    def get_session_id(self) -> str:
        return "session"

    def connect(self) -> None:
        self.calls.append(("connect", None))

    def disconnect(self) -> None:
        self.calls.append(("disconnect", None))

    def subscribe(self, *channels: object) -> None:
        self.calls.append(("subscribe", channels))

    def unsubscribe(self, **kwargs: object) -> None:
        self.calls.append(("unsubscribe", kwargs))


def test_subscription_fingerprint_is_order_independent() -> None:
    assert subscription_fingerprint(("msft", "AAPL")) == subscription_fingerprint(("AAPL", "MSFT"))
    assert subscription_fingerprint(("AAPL",)) != subscription_fingerprint(("MSFT",))


def test_observability_is_bounded_and_sanitized() -> None:
    diagnostics = PerformanceDiagnostics()
    for index in range(1000):
        diagnostics.record_stream_observability(
            "callback accepted",
            symbol="aapl",
            authorization="secret-token",
            sequence=index,
        )
    observed = diagnostics.stream_observability()
    assert observed["counts"]["CALLBACK_ACCEPTED"] == 1000
    assert len(observed["events"]) <= 512
    assert all("authorization" not in event for event in observed["events"])
    assert all(event["symbol"] == "AAPL" for event in observed["events"])


def test_stream_generation_and_subscription_transitions_are_diagnostic_only(monkeypatch) -> None:
    diagnostics = PerformanceDiagnostics()
    monkeypatch.setattr("app.webull.websocket_client.performance_diagnostics", diagnostics)
    sdk = _Sdk()
    backend = OfficialSdkStreamBackend(
        sdk,
        registration_grace_seconds=0,
        receive_timeout_seconds=0,
    )
    backend.connect()
    backend.subscribe(("AAPL", "MSFT"))
    backend.subscribe(("MSFT", "AAPL"))
    backend.subscribe(("AAPL", "TSLA"))

    evidence = diagnostics.stream_observability()
    counts = evidence["counts"]
    assert counts["CONNECT_REQUESTED"] == 1
    assert counts["CONNECT_SUCCEEDED"] == 1
    assert counts["SUBSCRIBE_COMPLETED"] == 2
    assert counts["SUBSCRIPTION_NO_CHANGE"] == 1
    assert counts["UNSUBSCRIBE_REQUESTED"] == 1
    assert counts["UNSUBSCRIBE_COMPLETED"] == 1
    assert backend.generation_metrics["generation"] == 1
    assert len(sdk.calls) == 4


def test_callback_evidence_records_generation_and_retired_client(monkeypatch) -> None:
    diagnostics = PerformanceDiagnostics()
    monkeypatch.setattr("app.webull.websocket_client.performance_diagnostics", diagnostics)
    sdk = _Sdk()
    backend = OfficialSdkStreamBackend(sdk, registration_grace_seconds=0)
    backend.connect()
    backend._on_quotes_message(sdk, "quotes", {"symbol": "AAPL"})
    backend._on_quotes_message(object(), "quotes", {"symbol": "AAPL"})
    events = diagnostics.stream_observability()["events"]
    assert any(event["event"] == "CALLBACK_ACCEPTED" and event["generation"] == 1 for event in events)
    assert any(event["event"] == "CALLBACK_REJECTED" and event["reason_category"] == "STALE_CLIENT" for event in events)


def test_diagnostic_sink_failure_cannot_change_backend_behavior(monkeypatch) -> None:
    diagnostics = PerformanceDiagnostics()
    diagnostics.record_stream_observability("MALFORMED", payload=object())
    monkeypatch.setattr("app.webull.websocket_client.performance_diagnostics", diagnostics)
    sdk = _Sdk()
    backend = OfficialSdkStreamBackend(sdk, registration_grace_seconds=0)
    backend.connect()
    assert backend.generation_metrics["generation"] == 1
