from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.webull import live_stream_smoke
from app.webull.sdk_streaming_adapter import WebullStreamingCredentials
from app.webull.websocket_client import OfficialSdkStreamBackend


NOW = datetime(2026, 8, 6, 16, 0, tzinfo=UTC)


class InvalidSession(Exception):
    error_code = "INVALID_SESSION"
    request_id = "request-safe-1"


class Client:
    def __init__(
        self,
        session_id: str,
        *,
        failures: int = 0,
        callback_session_id: str | None = None,
    ) -> None:
        self._client_id = session_id
        self._quotes_session_id = session_id
        self.api_client = object()
        self.failures = failures
        self.callback_session_id = callback_session_id or session_id
        self.on_quotes_message = None
        self.on_connect_success = None
        self.on_disconnect = None
        self.calls: list[object] = []

    @property
    def quotes_session_id(self) -> str:
        return self._quotes_session_id

    def connect_and_loop_start(self, **kwargs: object) -> None:
        self.calls.append("connect")
        self.on_connect_success(self, self.api_client, self.callback_session_id)

    def subscribe(self, *args: object, **kwargs: object) -> None:
        self.calls.append(("subscribe", args, kwargs, self._quotes_session_id))
        if self.failures:
            self.failures -= 1
            raise InvalidSession("not visible yet")

    def loop_stop(self) -> None:
        self.calls.append("loop_stop")

    def disconnect(self) -> None:
        self.calls.append("disconnect")


def backend(
    client: Client,
    *,
    factory=None,
    sleeps: list[float] | None = None,
    diagnostics: list[dict[str, object]] | None = None,
) -> OfficialSdkStreamBackend:
    result = OfficialSdkStreamBackend(
        client,
        subscription_mapper=lambda symbols: {
            "symbols": symbols,
            "category": "US_STOCK",
            "sub_types": ("QUOTE", "TICK"),
        },
        connect_timeout_seconds=0,
        registration_grace_seconds=1,
        registration_retry_backoff_seconds=2,
        sleeper=(sleeps if sleeps is not None else []).append,
        clock=lambda: NOW,
        sdk_client_factory=factory,
    )
    if diagnostics is not None:
        result.set_diagnostic_sink(diagnostics.append)
    return result


def test_subscribe_is_blocked_before_registration_ready() -> None:
    stream = backend(Client("session-one"))

    with pytest.raises(RuntimeError, match="registration is not ready"):
        stream.subscribe(("AAPL",))


def test_same_session_identity_owns_connect_registration_and_subscription() -> None:
    sdk = Client("session-one")
    diagnostics: list[dict[str, object]] = []
    stream = backend(sdk, diagnostics=diagnostics)

    stream.connect()
    stream.subscribe(("AAPL",))

    subscribe_call = next(call for call in sdk.calls if isinstance(call, tuple))
    assert subscribe_call[-1] == "session-one"
    assert stream.registration_ready is True
    assert stream.subscription_acknowledged is True
    assert len({item["session_id_hash"] for item in diagnostics}) == 1
    assert "session-one" not in repr(diagnostics)


def test_stale_session_identity_is_rejected_and_replaced_once() -> None:
    stale = Client("stale-session", callback_session_id="different-session")
    fresh = Client("fresh-session")
    stream = backend(stale, factory=lambda: fresh)

    stream.connect()

    assert stream.client is fresh
    assert stream.registration_ready is True
    assert stale.calls[:3] == ["connect", "loop_stop", "disconnect"]
    assert fresh.calls == ["connect"]


def test_invalid_session_gets_one_retry_without_duplicate_subscription() -> None:
    sdk = Client("session-one", failures=1)
    sleeps: list[float] = []
    diagnostics: list[dict[str, object]] = []
    stream = backend(sdk, sleeps=sleeps, diagnostics=diagnostics)
    stream.connect()

    stream.subscribe(("AAPL",))
    stream.subscribe(("AAPL",))

    subscriptions = [call for call in sdk.calls if isinstance(call, tuple)]
    assert len(subscriptions) == 2
    assert sleeps == [1, 2]
    assert [item["session_lifecycle_phase"] for item in diagnostics].count(
        "REGISTRATION_REQUEST_FAILED"
    ) == 1
    assert diagnostics[-1]["session_lifecycle_phase"] == "DUPLICATE_SUBSCRIPTION_SKIPPED"


def test_invalid_session_retry_is_bounded() -> None:
    sdk = Client("session-one", failures=2)
    stream = backend(sdk)
    stream.connect()

    with pytest.raises(InvalidSession):
        stream.subscribe(("AAPL",))

    assert len([call for call in sdk.calls if isinstance(call, tuple)]) == 2
    assert stream.subscription_acknowledged is False


def test_reconnect_creates_fresh_identity_and_disconnect_invalidates_old() -> None:
    first = Client("session-one")
    second = Client("session-two")
    stream = backend(first, factory=lambda: second)
    stream.connect()
    stream.subscribe(("AAPL",))
    stream.disconnect()

    assert stream.registration_ready is False
    assert stream.subscription_acknowledged is False

    stream.connect()

    assert stream.client is second
    assert first.quotes_session_id != second.quotes_session_id
    assert stream.registration_ready is True


def test_callbacks_from_another_client_cannot_cross_use_session() -> None:
    first = Client("session-one")
    second = Client("session-two")
    stream = backend(first, factory=lambda: second)
    stream.connect()
    stream.disconnect()
    stream.connect()

    first.on_quotes_message(first, "quote", object())

    assert stream.receive() is None
    assert stream.client is second


def test_callback_captures_receive_timestamp_before_queueing() -> None:
    sdk = Client("session-one")
    stream = backend(sdk)
    stream.connect()

    sdk.on_quotes_message(sdk, "quote", object())

    received = stream.receive()
    assert received.received_timestamp == NOW
    assert received.topic == "quote"


def test_successful_registration_permits_subscription_only_after_grace() -> None:
    sdk = Client("session-one")
    sleeps: list[float] = []
    stream = backend(sdk, sleeps=sleeps)

    stream.connect()
    stream.subscribe(("AAPL",))

    assert sleeps == [1]
    assert sdk.calls[0] == "connect"
    assert sdk.calls[1][0] == "subscribe"


def test_live_smoke_waits_for_registration_readiness(monkeypatch) -> None:
    calls: list[str] = []
    captured: dict[str, object] = {}

    class SmokeBackend:
        registration_ready = False

        def connect(self) -> None:
            calls.append("connect")
            self.registration_ready = True

        def subscribe(self, symbols: tuple[str, ...]) -> None:
            assert self.registration_ready
            calls.append("subscribe")

        def receive(self):
            return None

        def disconnect(self) -> None:
            calls.append("disconnect")

    smoke = SmokeBackend()
    monkeypatch.setattr(
        live_stream_smoke,
        "_load_credentials",
        lambda: WebullStreamingCredentials("key", "secret", "session"),
    )
    monkeypatch.setattr(
        live_stream_smoke,
        "create_official_stream_backend",
        lambda *args, **kwargs: captured.update(kwargs) or smoke,
    )
    monkeypatch.setattr(live_stream_smoke, "_subscription_types", lambda: ("QUOTE",))
    monkeypatch.setattr(
        live_stream_smoke,
        "environ",
        {
            "WEBULL_API_BASE_URL": "https://api.sandbox.webull.com",
            "WEBULL_STREAM_URL": "mqtts://data-api.sandbox.webull.com:1883",
        },
    )

    assert live_stream_smoke.run("AAPL", 0) == 2
    assert calls == ["connect", "subscribe", "disconnect"]
    assert captured["http_host"] == "api.sandbox.webull.com"
    assert captured["mqtt_host"] == "data-api.sandbox.webull.com"
