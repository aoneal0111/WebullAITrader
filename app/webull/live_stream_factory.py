"""Production Webull market-data stream composition."""

from __future__ import annotations

import time
from decimal import Decimal

from app.webull.configuration import ReconnectPolicy
from app.webull.logging import StructuredLogger
from app.webull.market_event_parser import WebullMarketEventParser
from app.webull.sdk_streaming_adapter import (
    WebullStreamingCredentials,
    WebullMarketSubscription,
    create_official_stream_backend,
)
from app.webull.websocket_client import WebullWebSocketClient


class ConsoleSink:
    def emit(self, record: object) -> None:
        print(record, flush=True)


def sleep_decimal(seconds: Decimal) -> None:
    time.sleep(float(seconds))


def create_desktop_live_market_stream(
    *,
    subscription: WebullMarketSubscription,
) -> WebullWebSocketClient:
    """
    Construct the production Webull market-data client.

    This function assembles existing production components only.
    """

    credentials = WebullStreamingCredentials.from_environment()

    backend = create_official_stream_backend(
        credentials=credentials,
        subscription=subscription,
    )

    parser = WebullMarketEventParser()

    logger = StructuredLogger(ConsoleSink())

    reconnect_policy = ReconnectPolicy(
        maximum_attempts=5,
        backoff_seconds=Decimal("1"),
    )

    return WebullWebSocketClient(
        backend=backend,
        parser=parser,
        reconnect_policy=reconnect_policy,
        sleeper=sleep_decimal,
        logger=logger,
    )


__all__ = [
    "create_desktop_live_market_stream",
]