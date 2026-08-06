"""Webull implementation of the broker-plugin contract."""

from __future__ import annotations

from dataclasses import dataclass

from app.broker_plugins.models import (
    BrokerCapabilities,
    BrokerRuntime,
)
from app.broker_plugins.webull.composition import (
    WebullBrokerFactory,
    WebullMarketDataFactory,
    create_webull_runtime,
)


WEBULL_CAPABILITIES = BrokerCapabilities(
    provider="webull",
    version="1.0",
    supports_execution=True,
    supports_market_data=True,
    supports_scanner=False,
    supports_account_data=True,
    supports_live_trading=True,
    supports_streaming=True,
    supports_stocks=True,
    supports_options=False,
    supports_crypto=False,
    supports_futures=False,
    supports_forex=False,
    supports_regular_session=True,
    supports_premarket_session=True,
    supports_after_hours_session=True,
    supports_overnight_session=True,
    supports_shorting=False,
)


@dataclass(frozen=True, slots=True)
class WebullBrokerPlugin:
    """Compose Webull services behind broker-neutral application contracts."""

    broker_factory: WebullBrokerFactory
    market_data_factory: WebullMarketDataFactory | None = None

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
            market_data_factory=self.market_data_factory,
        )


__all__ = [
    "WEBULL_CAPABILITIES",
    "WebullBrokerPlugin",
]
