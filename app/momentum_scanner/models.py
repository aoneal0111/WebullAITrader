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

