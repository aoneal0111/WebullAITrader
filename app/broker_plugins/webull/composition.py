"""Webull broker-plugin composition."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.broker_plugins.models import BrokerRuntime
from app.broker_protocol.protocol import Broker
from app.market_data.transport import MarketDataTransport


WebullBrokerFactory = Callable[[Any], Broker]
WebullMarketDataFactory = Callable[
    [Any],
    MarketDataTransport | None,
]


def create_webull_runtime(
    configuration: object,
    *,
    broker_factory: WebullBrokerFactory,
    market_data_factory: WebullMarketDataFactory | None = None,
) -> BrokerRuntime:
    """Create a broker-neutral runtime backed by Webull execution services."""

    if not callable(broker_factory):
        raise ValueError("broker_factory must be callable")

    broker = broker_factory(configuration)
    market_data = (
        market_data_factory(configuration)
        if market_data_factory is not None
        else None
    )

    if broker is None:
        raise ValueError("Webull broker factory returned no broker")

    from app.broker_plugins.webull.plugin import WEBULL_CAPABILITIES

    return BrokerRuntime(
        provider="webull",
        capabilities=WEBULL_CAPABILITIES,
        execution=broker,
        market_data=market_data,
    )


__all__ = [
    "WebullBrokerFactory",
    "WebullMarketDataFactory",
    "create_webull_runtime",
]
