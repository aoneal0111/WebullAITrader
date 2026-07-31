from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.momentum_scanner.models import AssetClass

ZERO = Decimal("0")


class SecurityType(StrEnum):
    COMMON_STOCK = "COMMON_STOCK"
    ETF = "ETF"
    ADR = "ADR"
    REIT = "REIT"
    PREFERRED = "PREFERRED"
    WARRANT = "WARRANT"
    CRYPTO_PAIR = "CRYPTO_PAIR"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class UniverseSymbol:
    symbol: str
    asset_class: AssetClass
    exchange: str
    security_type: SecurityType
    tradable: bool
    api_symbol: str | None = None
    instrument_id: str | None = None
    category: str | None = None
    source: str = "UNKNOWN"
    region: str | None = None
    tradable_status: str | None = None
    halted: bool = False
    price: Decimal | None = None
    average_30_day_volume: Decimal | None = None
    quote_currency: str | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        api_symbol = (
            self.api_symbol.strip().upper()
            if self.api_symbol and self.api_symbol.strip()
            else symbol
        )
        exchange = self.exchange.strip().upper()

        if not symbol:
            raise ValueError("symbol is required")

        if not exchange:
            raise ValueError("exchange is required")

        if self.price is not None and self.price <= ZERO:
            raise ValueError("price must be positive when supplied")

        if (
            self.average_30_day_volume is not None
            and self.average_30_day_volume < ZERO
        ):
            raise ValueError(
                "average_30_day_volume cannot be negative"
            )

        quote_currency = (
            self.quote_currency.strip().upper()
            if self.quote_currency
            and self.quote_currency.strip()
            else None
        )
        instrument_id = _optional_text(self.instrument_id)
        category = _optional_upper(self.category)
        source = self.source.strip().upper()
        region = _optional_lower(self.region)
        tradable_status = _optional_upper(self.tradable_status)

        if not api_symbol:
            raise ValueError("api_symbol is required")

        if not source:
            raise ValueError("source is required")

        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "api_symbol", api_symbol)
        object.__setattr__(self, "instrument_id", instrument_id)
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "region", region)
        object.__setattr__(self, "tradable_status", tradable_status)
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(
            self,
            "quote_currency",
            quote_currency,
        )

    @property
    def display_symbol(self) -> str:
        return self.symbol

    @property
    def request_key(self) -> tuple[str, str]:
        return (self.category or self.asset_class.value, self.api_symbol or self.symbol)


@dataclass(frozen=True, slots=True)
class UniverseSelection:
    included: tuple[UniverseSymbol, ...]
    excluded: tuple[UniverseSymbol, ...]

    @property
    def included_symbols(self) -> tuple[str, ...]:
        return tuple(item.symbol for item in self.included)

    @property
    def excluded_symbols(self) -> tuple[str, ...]:
        return tuple(item.symbol for item in self.excluded)


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_upper(value: str | None) -> str | None:
    normalized = _optional_text(value)
    return normalized.upper() if normalized is not None else None


def _optional_lower(value: str | None) -> str | None:
    normalized = _optional_text(value)
    return normalized.lower() if normalized is not None else None
