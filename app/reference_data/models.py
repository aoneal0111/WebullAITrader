from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.momentum_scanner.models import (
    AssetClass,
    CatalystStatus,
    CatalystType,
    FloatProvenance,
)

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class ReferenceRecord:
    symbol: str
    asset_class: AssetClass
    exchange: str | None
    previous_close: Decimal
    average_30_day_volume: Decimal
    float_shares: Decimal | None
    market_cap: Decimal | None
    shares_outstanding: Decimal | None
    tradable: bool
    float_provenance: FloatProvenance = FloatProvenance.AUTHORITATIVE_FLOAT
    catalyst: CatalystType = CatalystType.NONE
    catalyst_headline: str | None = None
    catalyst_status: CatalystStatus = CatalystStatus.UNKNOWN
    as_of: datetime = datetime.min.replace(tzinfo=UTC)
    current_volume: Decimal | None = None
    catalyst_source: str | None = None
    catalyst_published_at: datetime | None = None
    catalyst_source_url: str | None = None
    corroborating_sources: tuple[str, ...] = ()
    catalyst_evidence_count: int = 0
    catalyst_event_count: int = 0

    def __post_init__(self) -> None:
        normalized_symbol = self.symbol.strip().upper()
        if not normalized_symbol:
            raise ValueError("symbol is required")

        if self.previous_close <= ZERO:
            raise ValueError("previous_close must be positive")

        if self.average_30_day_volume <= ZERO:
            raise ValueError("average_30_day_volume must be positive")

        optional_positive_fields = {
            "float_shares": self.float_shares,
            "market_cap": self.market_cap,
            "shares_outstanding": self.shares_outstanding,
        }

        for field_name, value in optional_positive_fields.items():
            if value is not None and value <= ZERO:
                raise ValueError(f"{field_name} must be positive when supplied")
        if self.current_volume is not None and self.current_volume < ZERO:
            raise ValueError("current_volume must be non-negative when supplied")

        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")

        normalized_exchange = (
            self.exchange.strip().upper()
            if self.exchange and self.exchange.strip()
            else None
        )

        normalized_headline = (
            self.catalyst_headline.strip()
            if self.catalyst_headline
            and self.catalyst_headline.strip()
            else None
        )

        object.__setattr__(self, "symbol", normalized_symbol)
        object.__setattr__(self, "exchange", normalized_exchange)
        object.__setattr__(
            self,
            "catalyst_headline",
            normalized_headline,
        )


@dataclass(frozen=True, slots=True)
class ReferenceDataPolicy:
    stock_ttl: timedelta = timedelta(days=1)
    crypto_ttl: timedelta = timedelta(minutes=15)

    def ttl_for(self, asset_class: AssetClass) -> timedelta:
        if asset_class is AssetClass.CRYPTO:
            return self.crypto_ttl

        return self.stock_ttl
