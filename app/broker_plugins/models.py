"""Broker-neutral models exposed by broker plugins."""

from __future__ import annotations

from dataclasses import dataclass

from app.broker_protocol.protocol import Broker
from app.live_scanner.protocols import LiveScannerEngine
from app.market_data.transport import MarketDataTransport
from app.realtime_scanner.protocols import ReferenceLoader, UniverseSelector


def normalize_provider(value: str) -> str:
    """Return the canonical registry key for a broker provider."""

    if not isinstance(value, str):
        raise ValueError("broker provider must be a string")

    normalized = value.strip().casefold()

    if not normalized:
        raise ValueError("broker provider is required")

    if not all(character.isalnum() or character in {"-", "_"} for character in normalized):
        raise ValueError(
            "broker provider may contain only letters, numbers, hyphens, and underscores"
        )

    return normalized


@dataclass(frozen=True, slots=True)
class BrokerCapabilities:
    """Static capabilities advertised by a broker plugin."""

    provider: str
    version: str
    supports_execution: bool = False
    supports_market_data: bool = False
    supports_scanner: bool = False
    supports_account_data: bool = False
    supports_live_trading: bool = False
    supports_streaming: bool = False
    supports_options: bool = False
    supports_stocks: bool = False
    supports_crypto: bool = False
    supports_futures: bool = False
    supports_forex: bool = False
    supports_regular_session: bool = False
    supports_premarket_session: bool = False
    supports_after_hours_session: bool = False
    supports_overnight_session: bool = False
    supports_shorting: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", normalize_provider(self.provider))

        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("broker plugin version is required")

        object.__setattr__(self, "version", self.version.strip())

        boolean_fields = (
            "supports_execution",
            "supports_market_data",
            "supports_scanner",
            "supports_account_data",
            "supports_live_trading",
            "supports_streaming",
            "supports_options",
            "supports_stocks",
            "supports_crypto",
            "supports_futures",
            "supports_forex",
            "supports_regular_session",
            "supports_premarket_session",
            "supports_after_hours_session",
            "supports_overnight_session",
            "supports_shorting",
        )

        for field_name in boolean_fields:
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be boolean")


@dataclass(frozen=True, slots=True)
class BrokerRuntime:
    """Broker-neutral services composed by one broker plugin."""

    provider: str
    capabilities: BrokerCapabilities
    execution: Broker | None = None
    market_data: MarketDataTransport | None = None
    scanner: LiveScannerEngine | None = None
    universe_provider: UniverseSelector | None = None
    reference_data_provider: ReferenceLoader | None = None

    def __post_init__(self) -> None:
        normalized_provider = normalize_provider(self.provider)
        object.__setattr__(self, "provider", normalized_provider)

        if not isinstance(self.capabilities, BrokerCapabilities):
            raise ValueError("BrokerCapabilities is required")

        if self.capabilities.provider != normalized_provider:
            raise ValueError(
                "runtime provider must match the capability provider"
            )

        if self.execution is not None and not self.capabilities.supports_execution:
            raise ValueError(
                "execution service supplied without execution capability"
            )

        if self.market_data is not None and not self.capabilities.supports_market_data:
            raise ValueError(
                "market-data service supplied without market-data capability"
            )

        if self.scanner is not None and not self.capabilities.supports_scanner:
            raise ValueError(
                "scanner service supplied without scanner capability"
            )
