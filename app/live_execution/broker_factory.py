"""Production composition for the existing Webull execution broker."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlparse

from app.live_execution.webull_adapter import WebullAdapter
from app.live_scanner.transport import ReceiveTransportAdapter
from app.webull.configuration import (
    ReconnectPolicy,
    RetryPolicy,
    WebSocketSettings,
    WebullConfiguration,
)
from app.webull.http_client import (
    WebullHttpClient,
    create_official_trade_client,
)
from app.webull.logging import StructuredLogger
from app.webull.rate_limits import DeterministicRateLimiter, RateLimit
from app.webull.market_event_parser import WebullMarketEventParser
from app.webull.sdk_streaming_adapter import (
    WebullMarketSubscription,
    WebullStreamingCredentials,
    create_official_market_subscription,
    create_official_stream_backend,
)
from app.webull.stream_endpoint import parse_webull_stream_url
from app.webull.transport import WebullBrokerTransport
from app.webull.websocket_client import WebullWebSocketClient


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def monotonic_decimal() -> Decimal:
    return Decimal(str(time.monotonic()))


def sleep_decimal(seconds: Decimal) -> None:
    time.sleep(float(seconds))


class ConsoleSink:
    def emit(self, record: object) -> None:
        print(record, flush=True)


def build_webull_broker(configuration) -> WebullAdapter:
    """Build the existing Webull execution broker."""

    transport_logger = StructuredLogger(ConsoleSink())

    webull_configuration = WebullConfiguration(
        api_endpoint=configuration.api_base_url.rstrip("/"),
        account_id=configuration.account_id,
        timeout_seconds=Decimal("10"),
        retry_policy=RetryPolicy(
            maximum_attempts=3,
            initial_backoff_seconds=Decimal("1"),
            multiplier=Decimal("2"),
            maximum_backoff_seconds=Decimal("5"),
        ),
        reconnect_policy=ReconnectPolicy(
            maximum_attempts=3,
            backoff_seconds=Decimal("1"),
        ),
        websocket=WebSocketSettings(
            endpoint=configuration.stream_url,
        ),
    )

    limiter = DeterministicRateLimiter(
        RateLimit(
            requests=10,
            window_seconds=Decimal("1"),
        ),
        monotonic_decimal,
        sleep_decimal,
    )

    trade_client = create_official_trade_client(
        app_key=configuration.api_key,
        app_secret=configuration.api_secret,
        endpoint=webull_configuration.api_endpoint,
        timeout_seconds=webull_configuration.timeout_seconds,
    )

    http_client = WebullHttpClient(
        trade_client=trade_client,
        limiter=limiter,
        logger=transport_logger,
    )

    transport = WebullBrokerTransport(
        webull_configuration,
        http_client,
        None,
        transport_logger,
        utc_now,
    )

    return WebullAdapter(transport)


def build_webull_market_data_stream(
    configuration,
    *,
    subscription_factory: Callable[
        [], WebullMarketSubscription
    ] = create_official_market_subscription,
    backend_factory: Callable[..., object] = create_official_stream_backend,
    client_factory: Callable[..., object] = WebullWebSocketClient,
    session_id_factory: Callable[[], str] = lambda: (
        f"atlas-{uuid.uuid4().hex}"
    ),
) -> ReceiveTransportAdapter | None:
    """Build the existing official Webull streaming stack when enabled."""

    if not configuration.market_data_streaming_enabled:
        return None
    credentials = WebullStreamingCredentials(
        app_key=configuration.api_key,
        app_secret=configuration.api_secret,
        session_id=session_id_factory(),
    )
    stream_endpoint = parse_webull_stream_url(configuration.stream_url)
    api_endpoint = urlparse(configuration.api_base_url)
    stream_logger = StructuredLogger(ConsoleSink())
    stream_logger.log(
        "stream_configuration",
        "selected",
        configured_stream_url=stream_endpoint.configured_stream_url,
        mqtt_host=stream_endpoint.mqtt_host,
        mqtt_port=stream_endpoint.mqtt_port,
        transport=stream_endpoint.transport,
        tls_enable=stream_endpoint.tls_enable,
        websocket_path=stream_endpoint.websocket_path,
        trading_environment=configuration.environment.value,
    )
    backend = backend_factory(
        credentials,
        subscription_factory(),
        receive_timeout_seconds=1.0,
        http_host=api_endpoint.hostname,
        mqtt_host=stream_endpoint.mqtt_host,
        mqtt_port=stream_endpoint.mqtt_port,
        tls_enable=stream_endpoint.tls_enable,
        transport=stream_endpoint.transport,
        websocket_path=stream_endpoint.websocket_path,
    )
    client = client_factory(
        backend,
        WebullMarketEventParser(clock=utc_now),
        ReconnectPolicy(
            maximum_attempts=configuration.stream_reconnect_attempts,
            backoff_seconds=(
                configuration.stream_reconnect_backoff_seconds
            ),
        ),
        sleep_decimal,
        stream_logger,
    )
    return ReceiveTransportAdapter(client)


__all__ = [
    "build_webull_broker",
    "build_webull_market_data_stream",
]
