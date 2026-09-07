"""Immutable, execution-ineligible contracts for crypto momentum research."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Mapping

from app.assets import AssetType


ZERO = Decimal("0")


class CryptoMarketSession(StrEnum):
    CONTINUOUS_24_7 = "CONTINUOUS_24_7"


class CryptoResearchRegime(StrEnum):
    ASIA = "ASIA"
    EUROPE = "EUROPE"
    US = "US"
    WEEKEND = "WEEKEND"


class CryptoMomentumEventType(StrEnum):
    RANGE_BREAKOUT = "RANGE_BREAKOUT"
    MOMENTUM_ACCELERATION = "MOMENTUM_ACCELERATION"
    VOLUME_EXPANSION = "VOLUME_EXPANSION"
    HIGH_BREAK = "HIGH_BREAK"
    PULLBACK_CONTINUATION = "PULLBACK_CONTINUATION"
    VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"
    REVERSAL_ATTEMPT = "REVERSAL_ATTEMPT"


@dataclass(frozen=True, slots=True)
class CryptoPair:
    base_asset: str
    quote_asset: str
    provider_symbol: str
    asset_type: AssetType = field(default=AssetType.CRYPTO, init=False)

    def __post_init__(self) -> None:
        base = _token(self.base_asset, "base_asset")
        quote = _token(self.quote_asset, "quote_asset")
        provider = self.provider_symbol.strip().upper()
        if not provider:
            raise ValueError("provider_symbol is required")
        object.__setattr__(self, "base_asset", base)
        object.__setattr__(self, "quote_asset", quote)
        object.__setattr__(self, "provider_symbol", provider)

    @property
    def canonical_symbol(self) -> str:
        return f"{self.base_asset}/{self.quote_asset}"

    @property
    def identity(self) -> tuple[AssetType, str]:
        return self.asset_type, self.canonical_symbol

    @classmethod
    def configured(cls, value: str) -> "CryptoPair":
        """Parse an explicit configured pair; compact tickers are rejected."""
        normalized = value.strip().upper()
        separators = tuple(item for item in ("/", "-", ":") if item in normalized)
        if len(separators) != 1:
            raise ValueError("configured crypto pairs require BASE/QUOTE identity")
        parts = normalized.split(separators[0])
        if len(parts) != 2:
            raise ValueError("configured crypto pair is malformed")
        base, quote = parts
        return cls(base, quote, f"{base}{quote}")


@dataclass(frozen=True, slots=True)
class CryptoObservation:
    pair: CryptoPair
    timestamp: datetime
    price: Decimal
    bid: Decimal | None
    ask: Decimal | None
    volume: Decimal | None
    high: Decimal | None = None
    low: Decimal | None = None
    source: str = "WEBULL_CRYPTO_SNAPSHOT"
    asset_type: AssetType = field(default=AssetType.CRYPTO, init=False)

    def __post_init__(self) -> None:
        timestamp = _aware(self.timestamp, "timestamp")
        if self.price <= ZERO:
            raise ValueError("price must be positive")
        if self.volume is not None and self.volume < ZERO:
            raise ValueError("volume cannot be negative")
        if self.bid is not None and self.bid <= ZERO:
            raise ValueError("bid must be positive")
        if self.ask is not None and self.ask <= ZERO:
            raise ValueError("ask must be positive")
        if self.bid is not None and self.ask is not None and self.ask < self.bid:
            raise ValueError("ask cannot be below bid")
        if self.high is not None and self.high <= ZERO:
            raise ValueError("high must be positive")
        if self.low is not None and self.low <= ZERO:
            raise ValueError("low must be positive")
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "source", _text(self.source, "source"))


@dataclass(frozen=True, slots=True)
class CryptoFeatures:
    percentage_change: Decimal | None
    short_window_acceleration: Decimal | None
    volume: Decimal | None
    notional_volume: Decimal | None
    volume_acceleration: Decimal | None
    relative_volume_time_of_week: Decimal | None
    spread: Decimal | None
    spread_percent: Decimal | None
    quote_freshness_seconds: Decimal
    volatility: Decimal | None
    high_breakout_distance_percent: Decimal | None
    range_expansion: Decimal | None
    trend_velocity: Decimal | None
    float_shares: None = None
    market_cap: None = None
    premarket_gap: None = None
    opening_range: None = None
    halt_status: None = None
    catalyst_status: None = None
    asset_type: AssetType = field(default=AssetType.CRYPTO, init=False)


@dataclass(frozen=True, slots=True)
class CryptoResearchDecision:
    schema_version: str
    pair: CryptoPair
    timestamp: datetime
    decision_cutoff: datetime
    price: Decimal
    bid: Decimal | None
    ask: Decimal | None
    spread: Decimal | None
    volume: Decimal | None
    features: CryptoFeatures
    event_types: tuple[CryptoMomentumEventType, ...]
    score: Decimal
    score_components: tuple[tuple[str, Decimal | None], ...]
    rank: int
    regime: CryptoResearchRegime
    session: CryptoMarketSession = CryptoMarketSession.CONTINUOUS_24_7
    asset_type: AssetType = field(default=AssetType.CRYPTO, init=False)
    research_only: bool = field(default=True, init=False)
    production_promoted: bool = field(default=False, init=False)
    selection_authorized: bool = field(default=False, init=False)
    execution_authorized: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _aware(self.timestamp, "timestamp"))
        object.__setattr__(
            self, "decision_cutoff", _aware(self.decision_cutoff, "decision_cutoff")
        )
        if self.timestamp > self.decision_cutoff:
            raise ValueError("observation exceeds decision cutoff")
        if not 0 <= self.score <= 100:
            raise ValueError("score must be between 0 and 100")
        if self.rank <= 0:
            raise ValueError("rank must be positive")

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "asset_type": self.asset_type.value,
            "canonical_symbol": self.pair.canonical_symbol,
            "provider_symbol": self.pair.provider_symbol,
            "base_asset": self.pair.base_asset,
            "quote_asset": self.pair.quote_asset,
            "timestamp": self.timestamp.isoformat(),
            "decision_cutoff": self.decision_cutoff.isoformat(),
            "price": str(self.price),
            "bid": _decimal_text(self.bid),
            "ask": _decimal_text(self.ask),
            "spread": _decimal_text(self.spread),
            "volume": _decimal_text(self.volume),
            "features": _mapping(self.features),
            "event_types": [item.value for item in self.event_types],
            "score": str(self.score),
            "score_components": {
                key: _decimal_text(value) for key, value in self.score_components
            },
            "rank": self.rank,
            "session": self.session.value,
            "research_regime": self.regime.value,
            "research_only": self.research_only,
            "production_promoted": self.production_promoted,
            "selection_authorized": self.selection_authorized,
            "execution_authorized": self.execution_authorized,
        }


def crypto_regime(at: datetime) -> CryptoResearchRegime:
    current = _aware(at, "regime timestamp")
    if current.weekday() >= 5:
        return CryptoResearchRegime.WEEKEND
    if current.hour < 7:
        return CryptoResearchRegime.ASIA
    if current.hour < 13:
        return CryptoResearchRegime.EUROPE
    return CryptoResearchRegime.US


def _mapping(value: CryptoFeatures) -> Mapping[str, object]:
    return {
        name: _decimal_text(getattr(value, name))
        for name in value.__dataclass_fields__
    }


def _decimal_text(value: object) -> str | None:
    return None if value is None else str(value)


def _token(value: str, name: str) -> str:
    normalized = value.strip().upper()
    if not normalized or not normalized.isalnum():
        raise ValueError(f"{name} must be an alphanumeric asset code")
    return normalized


def _text(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


__all__ = [
    "CryptoFeatures",
    "CryptoMarketSession",
    "CryptoMomentumEventType",
    "CryptoObservation",
    "CryptoPair",
    "CryptoResearchDecision",
    "CryptoResearchRegime",
    "crypto_regime",
]
