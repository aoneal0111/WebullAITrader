"""Immutable, zero-authority contracts for broad-market momentum research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class DiscoverySource(StrEnum):
    SESSION_GAINERS = "SESSION_GAINERS"
    RELATIVE_VOLUME_10D = "RELATIVE_VOLUME_10D"
    VOLUME_LEADERS = "VOLUME_LEADERS"
    TURNOVER_LEADERS = "TURNOVER_LEADERS"


class MomentumEvent(StrEnum):
    ABNORMAL_VOLUME_ACCELERATION = "ABNORMAL_VOLUME_ACCELERATION"
    PRICE_ACCELERATION = "PRICE_ACCELERATION"
    PREMARKET_BREAKOUT = "PREMARKET_BREAKOUT"
    SESSION_HIGH_BREAKOUT = "SESSION_HIGH_BREAKOUT"
    GAP_EXPANSION = "GAP_EXPANSION"
    LIQUIDITY_EMERGENCE = "LIQUIDITY_EMERGENCE"
    MOMENTUM_PERSISTENCE = "MOMENTUM_PERSISTENCE"
    MOMENTUM_REACCELERATION = "MOMENTUM_REACCELERATION"


class ProductionUniverseComparison(StrEnum):
    PRODUCTION_RETURNED_GAINERS = "PRODUCTION_RETURNED_GAINERS"
    PRODUCTION_RETURNED_RVOL = "PRODUCTION_RETURNED_RVOL"
    PRODUCTION_RETURNED_BOTH = "PRODUCTION_RETURNED_BOTH"
    PRODUCTION_NOT_RETURNED = "PRODUCTION_NOT_RETURNED"
    PRODUCTION_NORMALIZATION_REJECTED = "PRODUCTION_NORMALIZATION_REJECTED"
    PRODUCTION_REFERENCE_REJECTED = "PRODUCTION_REFERENCE_REJECTED"
    PRODUCTION_ADMITTED = "PRODUCTION_ADMITTED"
    PRODUCTION_SCANNER_REACHED = "PRODUCTION_SCANNER_REACHED"


@dataclass(frozen=True, slots=True)
class SourceMembership:
    source: DiscoverySource
    rank: int
    page_index: int

    def __post_init__(self) -> None:
        if self.rank < 1 or self.page_index < 1:
            raise ValueError("source rank and page index must be positive")


@dataclass(frozen=True, slots=True)
class BroadMarketSnapshot:
    """Point-in-time inputs; later outcomes are intentionally absent."""

    symbol: str
    decision_cutoff: datetime
    session: str
    memberships: tuple[SourceMembership, ...]
    price: Decimal
    previous_close: Decimal | None = None
    open_price: Decimal | None = None
    session_high: Decimal | None = None
    prior_session_high: Decimal | None = None
    volume: Decimal | None = None
    relative_volume: Decimal | None = None
    turnover: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    quote_timestamp: datetime | None = None
    recent_1m_change_percent: Decimal | None = None
    recent_5m_change_percent: Decimal | None = None
    volume_acceleration: Decimal | None = None
    fresh_high_count: int = 0
    first_acceleration_at: datetime | None = None
    production_stages: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        if not symbol or not self.session.strip():
            raise ValueError("discovery identity is required")
        _aware(self.decision_cutoff, "decision_cutoff")
        if self.price <= 0:
            raise ValueError("price must be positive")
        for name in ("quote_timestamp", "first_acceleration_at"):
            value = getattr(self, name)
            if value is not None:
                _aware(value, name)
                if value > self.decision_cutoff:
                    raise ValueError(f"{name} cannot exceed decision cutoff")
        for name in (
            "previous_close", "open_price", "session_high", "prior_session_high", "volume",
            "relative_volume", "turnover", "bid", "ask", "bid_size", "ask_size",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.bid is not None and self.ask is not None and self.ask < self.bid:
            raise ValueError("ask cannot be below bid")
        if self.fresh_high_count < 0:
            raise ValueError("fresh_high_count cannot be negative")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "session", self.session.strip().upper())
        object.__setattr__(self, "memberships", tuple(sorted(
            set(self.memberships), key=lambda item: (item.source.value, item.rank)
        )))
        object.__setattr__(self, "production_stages", tuple(sorted({
            value.strip().upper() for value in self.production_stages if value.strip()
        })))


@dataclass(frozen=True, slots=True)
class MomentumFeatures:
    change_percent: Decimal | None
    gap_percent: Decimal | None
    dollar_volume: Decimal | None
    distance_from_high_percent: Decimal | None
    spread: Decimal | None
    spread_percent: Decimal | None
    top_of_book_liquidity: Decimal | None
    quote_age_ms: Decimal | None
    momentum_persistence_seconds: int | None


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    name: str
    points: int
    available: bool
    reason: str


@dataclass(frozen=True, slots=True)
class DynamicMomentumObservation:
    observation_id: str
    episode_id: str
    snapshot: BroadMarketSnapshot
    features: MomentumFeatures
    events: tuple[MomentumEvent, ...]
    components: tuple[ScoreComponent, ...]
    shadow_score: int
    shadow_promote_to_full_analysis: bool
    promotion_reason: str
    production_comparison: ProductionUniverseComparison
    created_at: datetime
    research_only: bool = True
    production_promoted: bool = False
    selection_authorized: bool = False
    execution_authorized: bool = False

    def __post_init__(self) -> None:
        _aware(self.created_at, "created_at")
        if self.created_at < self.snapshot.decision_cutoff:
            raise ValueError("observation cannot precede cutoff")
        if not self.research_only or any((
            self.production_promoted, self.selection_authorized,
            self.execution_authorized,
        )):
            raise ValueError("dynamic discovery must remain research-only")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = [
    "BroadMarketSnapshot", "DiscoverySource", "DynamicMomentumObservation",
    "MomentumEvent", "MomentumFeatures", "ProductionUniverseComparison",
    "ScoreComponent", "SourceMembership",
]
