from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.momentum_scanner.models import CatalystType, ScannerObservation


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
    last_price: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    cumulative_volume: Decimal = Decimal("0")
    halted: bool = False

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()

        if not symbol:
            raise ValueError("symbol is required")

        if self.timestamp is not None and self.timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")

        if self.last_price is not None and self.last_price <= 0:
            raise ValueError("last_price must be positive")

        if self.bid is not None and self.bid <= 0:
            raise ValueError("bid must be positive")

        if self.ask is not None and self.ask <= 0:
            raise ValueError("ask must be positive")

        if self.bid is not None and self.ask is not None and self.ask < self.bid:
            raise ValueError("ask cannot be lower than bid")

        if self.cumulative_volume < 0:
            raise ValueError("cumulative_volume cannot be negative")

        object.__setattr__(self, "symbol", symbol)


@dataclass(frozen=True, slots=True)
class AdapterResult:
    state: SymbolScannerState
    observation: ScannerObservation | None
    missing_fields: tuple[str, ...] = ()
