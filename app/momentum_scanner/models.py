from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class AssetClass(StrEnum):
    STOCK = "STOCK"
    CRYPTO = "CRYPTO"


class CatalystType(StrEnum):
    EARNINGS = "EARNINGS"
    FDA = "FDA"
    CLINICAL_TRIAL = "CLINICAL_TRIAL"
    CONTRACT = "CONTRACT"
    ACQUISITION = "ACQUISITION"
    PARTNERSHIP = "PARTNERSHIP"
    SEC_FILING = "SEC_FILING"
    GUIDANCE = "GUIDANCE"
    OTHER = "OTHER"
    NONE = "NONE"


class FloatProvenance(StrEnum):
    AUTHORITATIVE_FLOAT = "AUTHORITATIVE_FLOAT"
    SHARES_OUTSTANDING = "SHARES_OUTSTANDING"
    MARKET_CAP_PRICE_PROXY = "MARKET_CAP_PRICE_PROXY"
    UNKNOWN = "UNKNOWN"


class CatalystStatus(StrEnum):
    """Availability of the evidence used to assign ``catalyst``."""

    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ScannerObservation:
    symbol: str
    timestamp: datetime
    price: Decimal
    previous_close: Decimal
    current_volume: Decimal
    average_30_day_volume: Decimal
    float_shares: Decimal | None
    bid: Decimal | None
    ask: Decimal | None
    catalyst: CatalystType
    catalyst_headline: str | None
    tradable: bool
    halted: bool
    asset_class: AssetClass = AssetClass.STOCK
    catalyst_status: CatalystStatus = CatalystStatus.UNKNOWN
    float_provenance: FloatProvenance = FloatProvenance.AUTHORITATIVE_FLOAT
    catalyst_source: str | None = None
    catalyst_published_at: datetime | None = None
    catalyst_source_url: str | None = None
    corroborating_sources: tuple[str, ...] = ()
    catalyst_evidence_count: int = 0
    catalyst_event_count: int = 0


@dataclass(frozen=True, slots=True)
class ScannerMetrics:
    percentage_change: Decimal
    relative_volume: Decimal
    dollar_volume: Decimal
    spread_percent: Decimal | None


@dataclass(frozen=True, slots=True)
class ScannerDecision:
    symbol: str
    qualified: bool
    score: int
    metrics: ScannerMetrics
    passed_rules: tuple[str, ...]
    failed_rules: tuple[str, ...]
    timestamp: datetime | None = None
    price: Decimal | None = None
    current_volume: Decimal | None = None
    catalyst: CatalystType = CatalystType.NONE
    catalyst_headline: str | None = None
    catalyst_status: CatalystStatus = CatalystStatus.UNKNOWN
    diagnostic_rule_values: tuple[tuple[str, str], ...] = ()
    technical_qualifies_without_catalyst: bool = False
    technical_passed_rules: tuple[str, ...] = ()
    technical_failed_rules: tuple[str, ...] = ()
    cohort_flags: tuple[str, ...] = ()
    previous_close: Decimal | None = None
    average_30_day_volume: Decimal | None = None
    float_shares: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    tradable: bool | None = None
    halted: bool | None = None
    catalyst_source: str | None = None
    catalyst_published_at: datetime | None = None
    catalyst_source_url: str | None = None
    corroborating_sources: tuple[str, ...] = ()
    catalyst_evidence_count: int = 0
    catalyst_event_count: int = 0
    scanner_rank: int | None = None

