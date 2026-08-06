"""Immutable, broker-independent capability contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class AssetCapability(StrEnum):
    STOCKS = "Stocks"
    OPTIONS = "Options"
    CRYPTO = "Crypto"
    FUTURES = "Futures"
    FOREX = "Forex"


class SessionCapability(StrEnum):
    REGULAR = "Regular"
    PREMARKET = "Premarket"
    AFTER_HOURS = "After Hours"
    OVERNIGHT = "Overnight"


class CapabilityAvailability(StrEnum):
    AVAILABLE = "Available"
    SUBSCRIPTION_REQUIRED = "Unavailable (Subscription Required)"
    BROKER_NOT_SUPPORTED = "Unavailable (Broker Not Supported)"
    CONFIGURATION_REQUIRED = "Unavailable (Configuration Required)"
    MARKET_CLOSED = "Unavailable (Market Closed)"
    UNKNOWN = "Unknown"


CapabilityName = AssetCapability | SessionCapability


@dataclass(frozen=True, slots=True)
class CapabilityEntry:
    name: CapabilityName
    availability: CapabilityAvailability

    def __post_init__(self) -> None:
        if not isinstance(self.name, (AssetCapability, SessionCapability)):
            raise TypeError("capability name must be a supported capability")
        if not isinstance(self.availability, CapabilityAvailability):
            raise TypeError("capability availability must be classified")


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
    assets: tuple[CapabilityEntry, ...]
    sessions: tuple[CapabilityEntry, ...]

    def __post_init__(self) -> None:
        _validate_entries(self.assets, AssetCapability, "assets")
        _validate_entries(self.sessions, SessionCapability, "sessions")

    @classmethod
    def unknown(cls) -> "CapabilitySnapshot":
        return cls(
            assets=tuple(
                CapabilityEntry(item, CapabilityAvailability.UNKNOWN)
                for item in AssetCapability
            ),
            sessions=tuple(
                CapabilityEntry(item, CapabilityAvailability.UNKNOWN)
                for item in SessionCapability
            ),
        )

    def availability_for(
        self,
        name: CapabilityName,
    ) -> CapabilityAvailability:
        for entry in (*self.assets, *self.sessions):
            if entry.name is name:
                return entry.availability
        return CapabilityAvailability.UNKNOWN


class RuntimeCapabilityProvider(Protocol):
    """Boundary implemented by broker adapters that detect capabilities."""

    def capability_snapshot(self) -> CapabilitySnapshot: ...


def _validate_entries(entries, expected_type, field_name: str) -> None:
    if not isinstance(entries, tuple):
        raise TypeError(f"capability {field_name} must be an immutable tuple")
    if any(
        not isinstance(entry, CapabilityEntry)
        or not isinstance(entry.name, expected_type)
        for entry in entries
    ):
        raise TypeError(f"capability {field_name} contain invalid entries")
    names = tuple(entry.name for entry in entries)
    if len(set(names)) != len(names):
        raise ValueError(f"capability {field_name} must be unique")
