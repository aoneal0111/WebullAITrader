from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.configuration import TradingEnvironment
from app.live_execution.broker_factory import build_webull_market_data_stream
from app.live_scanner.coordinator import LiveScannerCoordinator
from app.webull.sdk_streaming_adapter import (
    WebullMarketSubscription,
    WebullStreamingCredentials,
    create_official_stream_backend,
    create_official_market_subscription,
)
from app.webull.stream_endpoint import (
    WebullStreamEndpoint,
    parse_webull_stream_url,
)


SANDBOX_HOST = "data-api.sandbox.webull.com"


@pytest.mark.parametrize(
    ("url", "host", "port", "transport", "tls", "path"),
    (
        (
            f"wss://{SANDBOX_HOST}:8883/mqtt",
            SANDBOX_HOST,
            8883,
            "websockets",
            True,
            "/mqtt",
        ),
        (
            f"mqtts://{SANDBOX_HOST}:1883",
            SANDBOX_HOST,
            1883,
            "tcp",
            True,
            None,
        ),
        (
            f"ws://{SANDBOX_HOST}:8080/mqtt",
            SANDBOX_HOST,
            8080,
            "websockets",
            False,
            "/mqtt",
        ),
        (
            f"mqtt://{SANDBOX_HOST}",
            SANDBOX_HOST,
            1883,
            "tcp",
            False,
            None,
        ),
    ),
)
def test_stream_url_maps_to_official_sdk_configuration(
    url,
    host,
    port,
    transport,
    tls,
    path,
):
    endpoint = parse_webull_stream_url(url)

    assert endpoint.mqtt_host == host
    assert endpoint.mqtt_port == port
    assert endpoint.transport == transport
    assert endpoint.tls_enable is tls
    assert endpoint.websocket_path == path


def test_websocket_path_is_preserved_and_defaults_to_mqtt():
    custom = parse_webull_stream_url(
        f"wss://{SANDBOX_HOST}:8883/custom/quotes"
    )
    default = parse_webull_stream_url(f"wss://{SANDBOX_HOST}")

    assert custom.websocket_path == "/custom/quotes"
    assert default.websocket_path == "/mqtt"
    assert default.mqtt_port == 8883


@pytest.mark.parametrize(
    ("url", "message"),
    (
        ("https://data-api.sandbox.webull.com/mqtt", "unsupported"),
        ("wss:///mqtt", "hostname"),
        ("not-a-url", "unsupported"),
        ("wss://host:invalid/mqtt", "invalid"),
        ("wss://host:0/mqtt", "between 1 and 65535"),
        ("wss://host:70000/mqtt", "invalid"),
        ("mqtts://host/not-a-tcp-path", "must not include"),
        ("wss://user:password@host/mqtt", "must not include credentials"),
        ("wss://host/mqtt?token=secret", "must not include"),
    ),
)
def test_invalid_or_malformed_stream_urls_fail_closed(url, message):
    with pytest.raises(ValueError, match=message):
        parse_webull_stream_url(url)


def test_endpoint_model_rejects_scheme_transport_mismatches():
    with pytest.raises(ValueError, match="requires transport='websockets'"):
        WebullStreamEndpoint(
            configured_stream_url=f"wss://{SANDBOX_HOST}:8883/mqtt",
            scheme="wss",
            mqtt_host=SANDBOX_HOST,
            mqtt_port=8883,
            transport="tcp",
            tls_enable=True,
            websocket_path=None,
        )
    with pytest.raises(ValueError, match="requires transport='tcp'"):
        WebullStreamEndpoint(
            configured_stream_url=f"mqtts://{SANDBOX_HOST}:1883",
            scheme="mqtts",
            mqtt_host=SANDBOX_HOST,
            mqtt_port=1883,
            transport="websockets",
            tls_enable=True,
            websocket_path="/mqtt",
        )


def test_official_subscription_is_session_aware_with_injected_clock():
    regular = create_official_market_subscription(
        clock=lambda: datetime(2026, 7, 30, 15, tzinfo=UTC)
    )
    overnight = create_official_market_subscription(
        clock=lambda: datetime(2026, 7, 31, 1, tzinfo=UTC)
    )
    weekend = create_official_market_subscription(
        clock=lambda: datetime(2026, 8, 1, 15, tzinfo=UTC)
    )

    assert regular.sdk_arguments(("AAPL",))["overnight_required"] is False
    assert overnight.sdk_arguments(("AAPL",))["overnight_required"] is True
    assert weekend.sdk_arguments(("AAPL",))["overnight_required"] is False


class FakeSdkClient:
    def __init__(self, arguments):
        self.arguments = arguments
        self.calls = []
        self.on_quotes_message = None
        self.on_connect_success = None
        self.on_disconnect = None

    def ws_set_options(self, *, path):
        self.calls.append(("ws_set_options", path))

    def connect_and_loop_start(self):
        self.calls.append("connect")
        self.on_connect_success(self, object(), self.arguments["session_id"])

    def subscribe(self, **kwargs):
        self.calls.append(("subscribe", kwargs))

    def loop_stop(self):
        self.calls.append("loop_stop")

    def disconnect(self):
        self.calls.append("disconnect")


def credentials():
    return WebullStreamingCredentials(
        app_key="app-key",
        app_secret="app-secret",
        session_id="atlas-test",
    )


def subscription():
    return WebullMarketSubscription(
        category="US_STOCK",
        sub_types=("QUOTE", "SNAPSHOT", "TICK"),
    )


def test_data_streaming_client_receives_websockets_and_path_before_connect():
    observed = {}

    def factory(**kwargs):
        client = FakeSdkClient(kwargs)
        observed["client"] = client
        return client

    backend = create_official_stream_backend(
        credentials(),
        subscription(),
        client_factory=factory,
        mqtt_host=SANDBOX_HOST,
        mqtt_port=8883,
        tls_enable=True,
        transport="websockets",
        websocket_path="/custom",
    )

    assert observed["client"].arguments["transport"] == "websockets"
    assert observed["client"].calls == [("ws_set_options", "/custom")]

    backend.connect()

    assert observed["client"].calls[:2] == [
        ("ws_set_options", "/custom"),
        "connect",
    ]


def test_existing_tcp_sdk_configuration_remains_valid():
    observed = {}

    def factory(**kwargs):
        client = FakeSdkClient(kwargs)
        observed["client"] = client
        return client

    create_official_stream_backend(
        credentials(),
        subscription(),
        client_factory=factory,
        mqtt_host=SANDBOX_HOST,
        mqtt_port=1883,
        tls_enable=True,
        transport="tcp",
    )

    assert observed["client"].arguments["transport"] == "tcp"
    assert observed["client"].arguments["tls_enable"] is True
    assert observed["client"].calls == []


def configuration(stream_url=f"wss://{SANDBOX_HOST}:8883/mqtt"):
    return SimpleNamespace(
        market_data_streaming_enabled=True,
        api_key="safe-app-key",
        api_secret="never-log-this-app-secret",
        stream_url=stream_url,
        api_base_url="https://api.sandbox.webull.com",
        stream_reconnect_attempts=1,
        stream_reconnect_backoff_seconds=1,
        environment=TradingEnvironment.SANDBOX,
    )


def test_stream_configuration_log_contains_transport_but_no_secrets(capsys):
    build_webull_market_data_stream(
        configuration(),
        subscription_factory=subscription,
        backend_factory=lambda *args, **kwargs: object(),
        client_factory=lambda *args: object(),
        session_id_factory=lambda: "atlas-log-test",
    )

    output = capsys.readouterr().out
    assert "configured_stream_url" in output
    assert "'transport': 'websockets'" in output
    assert "'websocket_path': '/mqtt'" in output
    assert "'trading_environment': 'SANDBOX'" in output
    assert "never-log-this-app-secret" not in output
    assert "safe-app-key" not in output


def test_wss_regression_connects_and_reaches_scanner_subscription_stage():
    observed = {}

    def sdk_factory(**kwargs):
        client = FakeSdkClient(kwargs)
        observed["sdk_client"] = client
        return client

    def backend_factory(credentials_value, subscription_value, **kwargs):
        observed["backend_kwargs"] = kwargs
        return create_official_stream_backend(
            credentials_value,
            subscription_value,
            client_factory=sdk_factory,
            **kwargs,
        )

    stream = build_webull_market_data_stream(
        configuration(),
        subscription_factory=subscription,
        backend_factory=backend_factory,
        session_id_factory=lambda: "atlas-regression",
    )

    class Engine:
        def refresh_universe(self, asset_classes, *, force_reference_refresh):
            return ("AAPL",)

    scanner = LiveScannerCoordinator(stream, Engine())
    assert scanner.start() == ("AAPL",)

    assert observed["backend_kwargs"]["transport"] == "websockets"
    assert observed["backend_kwargs"]["transport"] != "tcp"
    assert observed["backend_kwargs"]["tls_enable"] is True
    assert observed["backend_kwargs"]["mqtt_port"] == 8883
    assert observed["backend_kwargs"]["websocket_path"] == "/mqtt"
    assert observed["sdk_client"].calls[0] == (
        "ws_set_options",
        "/mqtt",
    )
    assert observed["sdk_client"].calls[1] == "connect"
    assert observed["sdk_client"].calls[2][0] == "subscribe"
    assert observed["sdk_client"].calls[2][1]["symbols"] == ("AAPL",)
