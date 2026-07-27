"""Application-facing broker runtime factory."""

from __future__ import annotations

from app.broker_plugins.builtins import create_builtin_broker_registry
from app.broker_plugins.models import BrokerRuntime
from app.broker_plugins.webull import WebullBrokerFactory


def create_broker_runtime(
    *,
    provider: str,
    configuration: object,
    webull_broker_factory: WebullBrokerFactory,
) -> BrokerRuntime:
    """Create a broker runtime for a configured provider."""

    registry = create_builtin_broker_registry(
        webull_broker_factory=webull_broker_factory,
    )

    return registry.create_runtime(
        provider,
        configuration,
    )


__all__ = [
    "create_broker_runtime",
]
