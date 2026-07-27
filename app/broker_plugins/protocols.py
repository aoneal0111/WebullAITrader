"""Contracts implemented by broker-specific plugins."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.broker_plugins.models import BrokerCapabilities, BrokerRuntime


@runtime_checkable
class BrokerPlugin(Protocol):
    """Factory contract implemented by each broker integration."""

    @property
    def provider(self) -> str:
        """Return the provider's canonical registry name."""

    @property
    def capabilities(self) -> BrokerCapabilities:
        """Return immutable provider capability metadata."""

    def create_runtime(self, configuration: object) -> BrokerRuntime:
        """Compose broker-specific services behind broker-neutral protocols."""
