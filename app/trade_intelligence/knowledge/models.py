"""Small immutable records for the offline Trading Knowledge Pack.

These records have no import path to broker, authorization, or runtime code.
Outcome fields are labels calculated after an episode has been frozen.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.opportunity_discovery.taxonomy import STRATEGY_TAXONOMY

EPISODE_SCHEMA_VERSION = 1
LABELING_VERSION = 1
DEDUPE_VERSION = 1
KNOWLEDGE_PACK_VERSION = "ATLAS_TRADING_KNOWLEDGE_V1"
ACTIVE_STRATEGIES = tuple(
    item.strategy_id for item in STRATEGY_TAXONOMY if item.availability.value == "ACTIVE"
)
HORIZONS_SECONDS = (60, 120, 300, 600, 900, 1800, 3600)
PERCENT_TARGETS = (1, 2, 3, 4, 5, 6, 8, 10, 15, 20)
R_TARGETS = ("0.5", "1", "1.5", "2", "3", "4", "5")


def _wire(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_wire(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _wire(item) for key, item in value.items()}
    return value


@dataclass(frozen=True, slots=True)
class HistoricalBar:
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    session: str
    provider: str = "UNKNOWN"
    feed: str = "UNKNOWN"
    source_timezone: str = "UTC"
    normalization_version: int = 1
    previous_close: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    float_shares: Decimal | None = None
    market_cap: Decimal | None = None
    halt_state: str | None = None
    catalyst_id: str | None = None
    trade_count: int | None = None
    provider_vwap: Decimal | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or not self.symbol or not self.session:
            raise ValueError("INVALID_TIMESTAMP")
        if min(self.open, self.high, self.low, self.close) <= 0 or self.volume < 0:
            raise ValueError("MALFORMED_BARS")
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close, self.high):
            raise ValueError("MALFORMED_BARS")


@dataclass(frozen=True, slots=True)
class KnowledgeEpisode:
    episode_id: str
    symbol: str
    trading_date: date
    session: str
    primary_strategy: str
    strategy_memberships: tuple[str, ...]
    setup_start_timestamp: datetime
    detected_timestamp: datetime
    trigger_timestamp: datetime
    trigger_price: Decimal
    structural_stop: Decimal
    risk_per_share: Decimal
    structural_anchor: str
    opportunity_anchor: str
    formation_reason_codes: tuple[str, ...]
    invalidation_rules: tuple[str, ...]
    price_at_detection: Decimal | None
    percentage_change: Decimal | None
    current_volume: Decimal | None
    relative_volume: Decimal | None
    average_reference_volume: Decimal | None
    dollar_volume: Decimal | None
    bid: Decimal | None
    ask: Decimal | None
    spread_percent: Decimal | None
    previous_close: Decimal | None
    gap_percent: Decimal | None
    premarket_high: Decimal | None
    session_high: Decimal | None
    opening_range_high: Decimal | None
    opening_range_low: Decimal | None
    vwap: Decimal | None
    bar_window: tuple[HistoricalBar, ...]
    provenance: dict[str, Any]
    outcomes: dict[str, Any]
    reentry: dict[str, Any] | None = None
    features: dict[str, Any] | None = None
    normalization_version: int = 1
    detector_version: str = "ATLAS_DISCOVERY_RULES_V1"

    def to_dict(self) -> dict[str, Any]:
        return _wire(asdict(self))


@dataclass(frozen=True, slots=True)
class QuarantineRecord:
    record_id: str
    symbol: str
    trading_date: str | None
    strategy: str | None
    detected_timestamp: str | None
    reason: str
    details: str
    provenance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return _wire(asdict(self))


@dataclass(frozen=True, slots=True)
class BuildSummary:
    raw_detections: dict[str, int]
    exact_duplicates: int
    near_duplicates: int
    quarantined: int
    accepted_unique: int
    strategy_memberships: int
    per_strategy: dict[str, dict[str, int]]
