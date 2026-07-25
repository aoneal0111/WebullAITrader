"""Webull implementation of the broker-plugin contract."""

from __future__ import annotations

from dataclasses import dataclass

from app.broker_plugins.models import (
    BrokerCapabilities,
    BrokerRuntime,
)
from app.broker_plugins.webull.composition import (
    WebullBrokerFactory,
    create_webull_runtime,
)


WEBULL_CAPABILITIES = BrokerCapabilities(
    provider="webull",
    version="1.0",
    supports_execution=True,
    supports_market_data=False,
    supports_scanner=False,
    supports_account_data=True,
    supports_live_trading=True,
    supports_streaming=False,
    supports_options=False,
    supports_shorting=False,
)


@dataclass(frozen=True, slots=True)
class WebullBrokerPlugin:
    """Compose Webull services behind broker-neutral application contracts."""

    broker_factory: WebullBrokerFactory

    @property
    def provider(self) -> str:
        return WEBULL_CAPABILITIES.provider

    @property
    def capabilities(self) -> BrokerCapabilities:
        return WEBULL_CAPABILITIES

    def create_runtime(
        self,
        configuration: object,
    ) -> BrokerRuntime:
        return create_webull_runtime(
            configuration,
            broker_factory=self.broker_factory,
        )


__all__ = [
    "WEBULL_CAPABILITIES",
    "WebullBrokerPlugin",
]
