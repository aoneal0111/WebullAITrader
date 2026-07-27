"""Webull broker-plugin composition."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.broker_plugins.models import BrokerRuntime
from app.broker_protocol.protocol import Broker


WebullBrokerFactory = Callable[[Any], Broker]


def create_webull_runtime(
    configuration: object,
    *,
    broker_factory: WebullBrokerFactory,
) -> BrokerRuntime:
    """Create a broker-neutral runtime backed by Webull execution services."""

    if not callable(broker_factory):
        raise ValueError("broker_factory must be callable")

    broker = broker_factory(configuration)

    if broker is None:
        raise ValueError("Webull broker factory returned no broker")

    from app.broker_plugins.webull.plugin import WEBULL_CAPABILITIES

    return BrokerRuntime(
        provider="webull",
        capabilities=WEBULL_CAPABILITIES,
        execution=broker,
    )


__all__ = [
    "WebullBrokerFactory",
    "create_webull_runtime",
]
