from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.momentum_scanner.models import AssetClass
from app.universe.models import (
    SecurityType,
    UniverseSymbol,
)

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class UniverseFilterConfig:
    stock_minimum_price: Decimal = Decimal("1")
    stock_maximum_price: Decimal = Decimal("20")
    stock_minimum_average_volume: Decimal = Decimal("500000")
    stock_exchanges: tuple[str, ...] = (
        "NASDAQ",
        "NYSE",
        "AMEX",
    )
    stock_security_types: tuple[SecurityType, ...] = (
        SecurityType.COMMON_STOCK,
    )
    crypto_quote_currencies: tuple[str, ...] = (
        "USD",
        "USDT",
        "USDC",
    )
    crypto_minimum_average_volume: Decimal = ZERO

    def __post_init__(self) -> None:
        if self.stock_minimum_price <= ZERO:
            raise ValueError(
                "stock_minimum_price must be positive"
            )

        if (
            self.stock_maximum_price
            < self.stock_minimum_price
        ):
            raise ValueError(
                "stock_maximum_price cannot be lower "
                "than stock_minimum_price"
            )

        if self.stock_minimum_average_volume < ZERO:
            raise ValueError(
                "stock_minimum_average_volume "
                "cannot be negative"
            )

        if self.crypto_minimum_average_volume < ZERO:
            raise ValueError(
                "crypto_minimum_average_volume "
                "cannot be negative"
            )


def is_eligible(
    item: UniverseSymbol,
    config: UniverseFilterConfig = UniverseFilterConfig(),
) -> bool:
    if not item.tradable or item.halted:
        return False

    if item.asset_class is AssetClass.CRYPTO:
        return _crypto_is_eligible(item, config)

    if item.asset_class is AssetClass.STOCK:
        return _stock_is_eligible(item, config)

    return False


def exclusion_reasons(
    item: UniverseSymbol,
    config: UniverseFilterConfig = UniverseFilterConfig(),
) -> tuple[str, ...]:
    reasons: list[str] = []

    if not item.tradable:
        reasons.append("not_tradable")

    if item.halted:
        reasons.append("halted")

    if item.asset_class is AssetClass.STOCK:
        _append_stock_reasons(item, config, reasons)
    elif item.asset_class is AssetClass.CRYPTO:
        _append_crypto_reasons(item, config, reasons)
    else:
        reasons.append("unsupported_asset_class")

    return tuple(reasons)


def _stock_is_eligible(
    item: UniverseSymbol,
    config: UniverseFilterConfig,
) -> bool:
    if item.exchange not in config.stock_exchanges:
        return False

    if item.security_type not in config.stock_security_types:
        return False

    if item.price is None:
        return False

    if not (
        config.stock_minimum_price
        <= item.price
        <= config.stock_maximum_price
    ):
        return False

    if item.average_30_day_volume is None:
        return False

    return (
        item.average_30_day_volume
        >= config.stock_minimum_average_volume
    )


def _crypto_is_eligible(
    item: UniverseSymbol,
    config: UniverseFilterConfig,
) -> bool:
    if item.security_type is not SecurityType.CRYPTO_PAIR:
        return False

    if item.quote_currency not in config.crypto_quote_currencies:
        return False

    if item.average_30_day_volume is None:
        return False

    # Crypto intentionally has no minimum or maximum price rule.
    return (
        item.average_30_day_volume
        >= config.crypto_minimum_average_volume
    )


def _append_stock_reasons(
    item: UniverseSymbol,
    config: UniverseFilterConfig,
    reasons: list[str],
) -> None:
    if item.exchange not in config.stock_exchanges:
        reasons.append("unsupported_exchange")

    if item.security_type not in config.stock_security_types:
        reasons.append("unsupported_security_type")

    if item.price is None:
        reasons.append("missing_price")
    elif not (
        config.stock_minimum_price
        <= item.price
        <= config.stock_maximum_price
    ):
        reasons.append("price_range")

    if item.average_30_day_volume is None:
        reasons.append("missing_average_volume")
    elif (
        item.average_30_day_volume
        < config.stock_minimum_average_volume
    ):
        reasons.append("average_volume")


def _append_crypto_reasons(
    item: UniverseSymbol,
    config: UniverseFilterConfig,
    reasons: list[str],
) -> None:
    if item.security_type is not SecurityType.CRYPTO_PAIR:
        reasons.append("unsupported_security_type")

    if item.quote_currency not in config.crypto_quote_currencies:
        reasons.append("unsupported_quote_currency")

    if item.average_30_day_volume is None:
        reasons.append("missing_average_volume")
    elif (
        item.average_30_day_volume
        < config.crypto_minimum_average_volume
    ):
        reasons.append("average_volume")
