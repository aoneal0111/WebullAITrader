"""Credential-isolated factories for official Webull SDK clients."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from app.configuration.models import MarketDataConfiguration, TradingConfiguration
from app.webull.http_client import create_official_trade_client
from app.webull.sdk_market_data import create_official_data_client
from app.webull.credential_identity import credential_fingerprint


@dataclass(frozen=True, slots=True)
class TradingClientFactory:
    configuration: TradingConfiguration
    builder: Callable[..., object] = create_official_trade_client

    def create(self, *, timeout_seconds: Decimal = Decimal("10")) -> object:
        if not isinstance(self.configuration, TradingConfiguration):
            raise TypeError("trading configuration is required")
        return self.builder(
            app_key=self.configuration.api_key,
            app_secret=self.configuration.api_secret,
            endpoint=self.configuration.api_base_url,
            timeout_seconds=timeout_seconds,
        )


@dataclass(frozen=True, slots=True)
class MarketDataClientFactory:
    configuration: MarketDataConfiguration
    builder: Callable[..., object] = create_official_data_client

    def create(self, *, timeout_seconds: float | None = None) -> object:
        if not isinstance(self.configuration, MarketDataConfiguration):
            raise TypeError("market-data configuration is required")
        values = dict(
            app_key=self.configuration.api_key,
            app_secret=self.configuration.api_secret,
            endpoint=self.configuration.api_base_url,
        )
        if timeout_seconds is not None:
            values["timeout_seconds"] = timeout_seconds
        return self.builder(**values)


def trading_configuration(configuration: object) -> TradingConfiguration:
    scoped = getattr(configuration, "trading", None)
    if scoped is not None:
        return scoped
    return TradingConfiguration(
        environment=configuration.environment,
        account_id=getattr(configuration, "account_id", ""),
        api_key=configuration.api_key,
        api_secret=configuration.api_secret,
        api_base_url=configuration.api_base_url,
        stream_url=configuration.stream_url,
    )


def market_data_configuration(configuration: object) -> MarketDataConfiguration:
    scoped = getattr(configuration, "market_data", None)
    if scoped is not None:
        return scoped
    return MarketDataConfiguration(
        environment=configuration.environment,
        api_key=configuration.api_key,
        api_secret=configuration.api_secret,
        api_base_url=configuration.api_base_url,
        stream_url=configuration.stream_url,
    )


def market_data_cache_scope(configuration: MarketDataConfiguration) -> tuple[str, str]:
    if not isinstance(configuration, MarketDataConfiguration):
        raise TypeError("market-data configuration is required")
    return (
        configuration.environment.value,
        credential_fingerprint(configuration.api_key, configuration.api_secret),
    )


__all__ = [
    "MarketDataClientFactory",
    "TradingClientFactory",
    "market_data_configuration",
    "market_data_cache_scope",
    "trading_configuration",
]
