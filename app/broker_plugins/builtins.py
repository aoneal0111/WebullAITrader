"""Composition for broker plugins shipped with the application."""

from __future__ import annotations

from app.broker_plugins.registry import BrokerPluginRegistry
from app.broker_plugins.webull import (
    WebullBrokerFactory,
    WebullBrokerPlugin,
)


def create_builtin_broker_registry(
    *,
    webull_broker_factory: WebullBrokerFactory,
) -> BrokerPluginRegistry:
    """Create a registry containing the application's built-in broker plugins.

    Concrete transport construction remains injected so this module does not
    depend on an application entry point or broker-specific environment access.
    """

    if not callable(webull_broker_factory):
        raise ValueError("webull_broker_factory must be callable")

    registry = BrokerPluginRegistry()
    registry.register(
        WebullBrokerPlugin(
            broker_factory=webull_broker_factory,
        )
    )
    return registry


__all__ = [
    "create_builtin_broker_registry",
]
