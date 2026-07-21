from decimal import Decimal

from app.momentum_scanner import AssetClass
from app.universe import (
    CompositeUniverseProvider,
    InMemoryUniverseProvider,
    SecurityType,
    UniverseService,
    UniverseSymbol,
    exclusion_reasons,
)

D = Decimal


def stock(
    symbol: str = "TEST",
    **overrides: object,
) -> UniverseSymbol:
    values: dict[str, object] = {
        "symbol": symbol,
        "asset_class": AssetClass.STOCK,
        "exchange": "NASDAQ",
        "security_type": SecurityType.COMMON_STOCK,
        "tradable": True,
        "halted": False,
        "price": D("5"),
        "average_30_day_volume": D("1000000"),
        "quote_currency": "USD",
    }
    values.update(overrides)
    return UniverseSymbol(**values)


def crypto(
    symbol: str = "BTCUSD",
    **overrides: object,
) -> UniverseSymbol:
    values: dict[str, object] = {
        "symbol": symbol,
        "asset_class": AssetClass.CRYPTO,
        "exchange": "CRYPTO",
        "security_type": SecurityType.CRYPTO_PAIR,
        "tradable": True,
        "halted": False,
        "price": D("100000"),
        "average_30_day_volume": D("10000"),
        "quote_currency": "USD",
    }
    values.update(overrides)
    return UniverseSymbol(**values)


def test_stock_between_one_and_twenty_is_included() -> None:
    provider = InMemoryUniverseProvider(
        (
            stock("LOW", price=D("1")),
            stock("HIGH", price=D("20")),
        )
    )

    selection = UniverseService(provider).select(
        AssetClass.STOCK
    )

    assert selection.included_symbols == (
        "HIGH",
        "LOW",
    )


def test_stock_below_one_is_excluded() -> None:
    item = stock(price=D("0.99"))
    provider = InMemoryUniverseProvider((item,))

    selection = UniverseService(provider).select(
        AssetClass.STOCK
    )

    assert selection.included == ()
    assert "price_range" in exclusion_reasons(item)


def test_stock_above_twenty_is_excluded() -> None:
    item = stock(price=D("20.01"))
    provider = InMemoryUniverseProvider((item,))

    selection = UniverseService(provider).select(
        AssetClass.STOCK
    )

    assert selection.included == ()
    assert "price_range" in exclusion_reasons(item)


def test_low_volume_stock_is_excluded() -> None:
    item = stock(
        average_30_day_volume=D("499999")
    )
    provider = InMemoryUniverseProvider((item,))

    selection = UniverseService(provider).select(
        AssetClass.STOCK
    )

    assert selection.included == ()
    assert "average_volume" in exclusion_reasons(item)


def test_etf_is_excluded_by_default() -> None:
    item = stock(
        security_type=SecurityType.ETF
    )
    provider = InMemoryUniverseProvider((item,))

    selection = UniverseService(provider).select(
        AssetClass.STOCK
    )

    assert selection.included == ()
    assert (
        "unsupported_security_type"
        in exclusion_reasons(item)
    )


def test_halted_stock_is_excluded() -> None:
    item = stock(halted=True)
    provider = InMemoryUniverseProvider((item,))

    selection = UniverseService(provider).select(
        AssetClass.STOCK
    )

    assert selection.included == ()
    assert "halted" in exclusion_reasons(item)


def test_crypto_has_no_maximum_price() -> None:
    provider = InMemoryUniverseProvider(
        (
            crypto(
                "BTCUSD",
                price=D("1000000"),
            ),
        )
    )

    selection = UniverseService(provider).select(
        AssetClass.CRYPTO
    )

    assert selection.included_symbols == ("BTCUSD",)


def test_crypto_has_no_minimum_price() -> None:
    provider = InMemoryUniverseProvider(
        (
            crypto(
                "DOGEUSD",
                price=D("0.000001"),
            ),
        )
    )

    selection = UniverseService(provider).select(
        AssetClass.CRYPTO
    )

    assert selection.included_symbols == ("DOGEUSD",)


def test_crypto_non_usd_pair_is_excluded() -> None:
    item = crypto(
        "BTCETH",
        quote_currency="ETH",
    )
    provider = InMemoryUniverseProvider((item,))

    selection = UniverseService(provider).select(
        AssetClass.CRYPTO
    )

    assert selection.included == ()
    assert (
        "unsupported_quote_currency"
        in exclusion_reasons(item)
    )


def test_composite_provider_merges_symbols() -> None:
    first = InMemoryUniverseProvider(
        (stock("AAA"),)
    )
    second = InMemoryUniverseProvider(
        (
            stock("BBB"),
            stock("AAA", price=D("6")),
        )
    )

    provider = CompositeUniverseProvider(
        (first, second)
    )

    symbols = provider.list_symbols(
        AssetClass.STOCK
    )

    assert tuple(item.symbol for item in symbols) == (
        "AAA",
        "BBB",
    )
    assert symbols[0].price == D("6")


def test_select_all_combines_stocks_and_crypto() -> None:
    provider = InMemoryUniverseProvider(
        (
            stock("AAA"),
            crypto("BTCUSD"),
        )
    )

    selection = UniverseService(
        provider
    ).select_all()

    assert selection.included_symbols == (
        "BTCUSD",
        "AAA",
    )
