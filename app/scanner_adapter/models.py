from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.momentum_scanner.models import (
    CatalystStatus,
    CatalystType,
    ScannerObservation,
    FloatProvenance,
)


@dataclass(frozen=True, slots=True)
class ScannerReferenceData:
    symbol: str
    previous_close: Decimal
    average_30_day_volume: Decimal
    float_shares: Decimal | None
    catalyst: CatalystType = CatalystType.NONE
    catalyst_headline: str | None = None
    tradable: bool = True
    updated_at: datetime | None = None
    catalyst_status: CatalystStatus = CatalystStatus.UNKNOWN
    current_volume: Decimal | None = None
    catalyst_source: str | None = None
    catalyst_published_at: datetime | None = None
    catalyst_source_url: str | None = None
    corroborating_sources: tuple[str, ...] = ()
    catalyst_evidence_count: int = 0
    catalyst_event_count: int = 0
    float_provenance: FloatProvenance = FloatProvenance.AUTHORITATIVE_FLOAT

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()

        if not symbol:
            raise ValueError("symbol is required")

        if self.previous_close <= 0:
            raise ValueError("previous_close must be positive")

        if self.average_30_day_volume <= 0:
            raise ValueError("average_30_day_volume must be positive")

        if self.float_shares is not None and self.float_shares <= 0:
            raise ValueError("float_shares must be positive when provided")
        if self.current_volume is not None and self.current_volume < 0:
            raise ValueError("current_volume must be non-negative when provided")

        if self.updated_at is not None and self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")

        object.__setattr__(self, "symbol", symbol)
        if self.catalyst_headline is not None:
            headline = self.catalyst_headline.strip()
            object.__setattr__(
                self,
                "catalyst_headline",
                headline or None,
            )


@dataclass(frozen=True, slots=True)
class SymbolScannerState:
    symbol: str
    timestamp: datetime | None = None
    quote_timestamp: datetime | None = None
    trade_timestamp: datetime | None = None
    snapshot_timestamp: datetime | None = None
    last_price_timestamp: datetime | None = None
    quote_received_timestamp: datetime | None = None
    last_price_received_timestamp: datetime | None = None
    last_price: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    cumulative_volume: Decimal = Decimal("0")
    halted: bool = False

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()

        if not symbol:
            raise ValueError("symbol is required")

        for name in (
            "timestamp", "quote_timestamp", "trade_timestamp", "snapshot_timestamp",
            "last_price_timestamp", "quote_received_timestamp",
            "last_price_received_timestamp",
        ):
            value = getattr(self, name)
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")

        if self.last_price is not None and self.last_price <= 0:
            raise ValueError("last_price must be positive")

        if self.bid is not None and self.bid <= 0:
            raise ValueError("bid must be positive")

        if self.ask is not None and self.ask <= 0:
            raise ValueError("ask must be positive")

        if self.bid is not None and self.ask is not None and self.ask < self.bid:
            raise ValueError("ask cannot be lower than bid")

        if self.bid_size is not None and self.bid_size < 0:
            raise ValueError("bid_size cannot be negative")

        if self.ask_size is not None and self.ask_size < 0:
            raise ValueError("ask_size cannot be negative")

        if self.cumulative_volume < 0:
            raise ValueError("cumulative_volume cannot be negative")

        object.__setattr__(self, "symbol", symbol)


@dataclass(frozen=True, slots=True)
class AdapterResult:
    state: SymbolScannerState
    observation: ScannerObservation | None
    missing_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QualificationDiagnostics:
    evaluated: int
    complete: int
    qualified: int
    rejection_counts: tuple[tuple[str, int], ...]
    catalyst_counts: tuple[tuple[str, int], ...]
    otherwise_qualified_with_catalyst: int
    near_qualified_symbols: tuple[str, ...] = ()
