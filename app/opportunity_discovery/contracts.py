"""Immutable research-only contracts for multi-strategy opportunity discovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256

DISCOVERY_SCHEMA_VERSION = 1
TAXONOMY_VERSION = "ATLAS_MOMENTUM_TAXONOMY_V1"
DETECTOR_VERSION = "ATLAS_DISCOVERY_RULES_V1"
MAX_COMPLETED_BARS = 64


class StrategyFamily(StrEnum):
    BREAKOUT = "BREAKOUT"
    PULLBACK = "PULLBACK"
    CONTINUATION = "CONTINUATION"
    RECLAIM = "RECLAIM"
    OPENING_MOMENTUM = "OPENING_MOMENTUM"
    GAP = "GAP"
    COMPRESSION_EXPANSION = "COMPRESSION_EXPANSION"
    REVERSAL_TO_MOMENTUM = "REVERSAL_TO_MOMENTUM"
    HALT_RESUMPTION = "HALT_RESUMPTION"
    SPECIALIZED = "SPECIALIZED"


class DetectorAvailability(StrEnum):
    ACTIVE = "ACTIVE"
    UNAVAILABLE_FEATURE = "UNAVAILABLE_FEATURE"
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"
    FUTURE_RESEARCH = "FUTURE_RESEARCH"


class DetectionState(StrEnum):
    NOT_DETECTED = "NOT_DETECTED"
    FORMING = "FORMING"
    DETECTED = "DETECTED"
    STRENGTHENING = "STRENGTHENING"
    WEAKENING = "WEAKENING"
    INVALIDATED = "INVALIDATED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    strategy_id: str
    strategy_version: str
    family: StrategyFamily
    name: str
    description: str
    required_features: tuple[str, ...]
    optional_features: tuple[str, ...]
    availability: DetectorAvailability
    unavailable_reason: str | None = None
    high_risk: bool = False
    research_only: bool = True

    def __post_init__(self) -> None:
        if not all((self.strategy_id, self.strategy_version, self.name, self.description)):
            raise ValueError("strategy metadata is required")
        if len(set(self.required_features)) != len(self.required_features):
            raise ValueError("required features must be unique")
        if not self.research_only:
            raise ValueError("discovery strategies must remain research-only")
        if self.availability is not DetectorAvailability.ACTIVE and not self.unavailable_reason:
            raise ValueError("unavailable detectors must disclose why")


@dataclass(frozen=True, slots=True)
class CompletedBar:
    symbol: str
    completed_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    session: str = "REGULAR"

    def __post_init__(self) -> None:
        if self.completed_at.tzinfo is None or not self.symbol.strip() or not self.session.strip():
            raise ValueError("bar identity and aware completion time are required")
        if min(self.open, self.high, self.low, self.close) <= 0 or self.volume < 0:
            raise ValueError("invalid completed bar")
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close, self.high):
            raise ValueError("inconsistent OHLC")


@dataclass(frozen=True, slots=True)
class FeatureCapabilities:
    completed_bars: bool = True
    impulse_history: bool = True
    pullback_history: bool = True
    session_hod: bool = True
    opening_range: bool = True
    premarket_history: bool = True
    authoritative_vwap: bool = False
    prior_day_levels: bool = False
    pullback_ordinal: bool = False
    prior_close: bool = False
    halt_resume_facts: bool = False


@dataclass(frozen=True, slots=True)
class DiscoveryContext:
    symbol: str
    session_date: date
    session: str
    decision_cutoff: datetime
    completed_bars: tuple[CompletedBar, ...]
    capabilities: FeatureCapabilities = FeatureCapabilities()
    prior_close: Decimal | None = None
    vwap: Decimal | None = None
    percentage_change: Decimal | None = None
    relative_volume: Decimal | None = None
    dollar_volume: Decimal | None = None
    spread_percent: Decimal | None = None
    float_shares: Decimal | None = None
    scanner_rank: int | None = None

    def __post_init__(self) -> None:
        if self.decision_cutoff.tzinfo is None or not self.symbol.strip() or not self.session.strip():
            raise ValueError("discovery context identity and aware cutoff are required")
        if len(self.completed_bars) > MAX_COMPLETED_BARS:
            raise ValueError("completed-bar context exceeds bounded limit")
        previous = None
        for bar in self.completed_bars:
            if bar.symbol.upper() != self.symbol.upper() or bar.completed_at > self.decision_cutoff:
                raise ValueError("anti-lookahead violation in completed-bar context")
            if previous is not None and bar.completed_at <= previous:
                raise ValueError("completed bars must be strictly ordered")
            previous = bar.completed_at
        if self.vwap is not None and not self.capabilities.authoritative_vwap:
            raise ValueError("VWAP cannot be supplied without authoritative capability")
        if self.prior_close is not None and not self.capabilities.prior_close:
            raise ValueError("prior close cannot be supplied without authoritative capability")


@dataclass(frozen=True, slots=True)
class Impulse:
    start_time: datetime
    end_time: datetime
    start_price: Decimal
    end_price: Decimal
    absolute_move: Decimal
    percentage_move: Decimal
    duration_bars: int
    green_bar_count: int
    red_bar_count: int
    peak_volume: Decimal
    average_volume: Decimal
    range_expansion: Decimal | None
    distance_to_hod_percent: Decimal

    @property
    def anchor(self) -> str:
        return f"{self.start_time.isoformat()}|{self.end_time.isoformat()}"


@dataclass(frozen=True, slots=True)
class Pullback:
    start_time: datetime
    end_time: datetime
    bars: int
    depth_absolute: Decimal
    depth_percent: Decimal
    depth_relative_to_impulse: Decimal | None
    red_bar_count: int
    green_bar_count: int
    lowest_price: Decimal
    higher_low: bool
    volume_contraction: Decimal | None
    range_contraction: Decimal | None
    distance_to_trigger_percent: Decimal
    distance_to_hod_percent: Decimal
    invalidated: bool


@dataclass(frozen=True, slots=True)
class ReferenceLevels:
    current_hod: Decimal | None
    premarket_high: Decimal | None
    opening_range_high: Decimal | None
    opening_range_low: Decimal | None
    recent_resistance: Decimal | None
    recent_support: Decimal | None
    impulse_high: Decimal | None
    impulse_low: Decimal | None
    consolidation_high: Decimal | None
    consolidation_low: Decimal | None
    prior_breakout_level: Decimal | None
    vwap: Decimal | None
    prior_close: Decimal | None


@dataclass(frozen=True, slots=True)
class StrategyDetection:
    strategy_id: str
    strategy_version: str
    family: StrategyFamily
    symbol: str
    session: str
    session_date: date
    decision_cutoff: datetime
    state: DetectionState
    setup_anchor: str
    opportunity_anchor: str
    reference_price: Decimal | None
    trigger_level: Decimal | None
    structural_stop: Decimal | None
    quality_components: tuple[tuple[str, Decimal], ...]
    required_features_observed: tuple[str, ...]
    optional_features_observed: tuple[str, ...]
    missing_features: tuple[str, ...]
    reason_codes: tuple[str, ...]
    research_only: bool = True

    def __post_init__(self) -> None:
        if not self.research_only:
            raise ValueError("research detection cannot authorize execution")
        if self.state is DetectionState.DETECTED and not self.opportunity_anchor:
            raise ValueError("detected strategy requires a structural opportunity anchor")
        if self.trigger_level is not None and self.structural_stop is not None and self.trigger_level <= self.structural_stop:
            raise ValueError("research plan trigger must exceed structural stop")

    @property
    def detector_episode_id(self) -> str:
        material = f"{self.strategy_id}|{self.symbol.upper()}|{self.session_date}|{self.session}|{self.setup_anchor}"
        return sha256(material.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class StrategyMembership:
    strategy_id: str
    strategy_version: str
    family: StrategyFamily
    state: DetectionState
    detector_episode_id: str
    setup_anchor: str
    reference_price: Decimal | None
    trigger_level: Decimal | None
    structural_stop: Decimal | None
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NormalizedOpportunity:
    opportunity_id: str
    symbol: str
    session_date: date
    session: str
    decision_cutoff: datetime
    structural_anchor: str
    primary_strategy_id: str
    memberships: tuple[StrategyMembership, ...]
    reference_price: Decimal | None
    structural_stop: Decimal | None
    complete_r_plan: bool
    research_only: bool = True

    def __post_init__(self) -> None:
        if not self.research_only or not self.memberships:
            raise ValueError("normalized opportunity is non-executable and requires membership")
        if len({item.strategy_id for item in self.memberships}) != len(self.memberships):
            raise ValueError("strategy memberships must be unique")


@dataclass(frozen=True, slots=True)
class DiscoveryBatch:
    detections: tuple[StrategyDetection, ...]
    new_detector_episodes: tuple[str, ...]
    opportunities: tuple[NormalizedOpportunity, ...]
    new_opportunity_ids: tuple[str, ...]
