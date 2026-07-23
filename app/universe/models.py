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
    halted: bool = False
    price: Decimal | None = None
    average_30_day_volume: Decimal | None = None
    quote_currency: str | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
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

        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(
            self,
            "quote_currency",
            quote_currency,
        )


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
