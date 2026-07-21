from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.momentum_scanner import AssetClass, CatalystType
from app.reference_data import (
    CompositeReferenceDataProvider,
    InMemoryReferenceDataProvider,
    ReferenceDataCache,
    ReferenceDataNotFoundError,
    ReferenceDataPolicy,
    ReferenceDataService,
    ReferenceRecord,
)

D = Decimal


def stock_record(
    **overrides: object,
) -> ReferenceRecord:
    values: dict[str, object] = {
        "symbol": "TEST",
        "asset_class": AssetClass.STOCK,
        "exchange": "NASDAQ",
        "previous_close": D("4"),
        "average_30_day_volume": D("1000000"),
        "float_shares": D("8000000"),
        "market_cap": D("40000000"),
        "shares_outstanding": D("10000000"),
        "tradable": True,
        "catalyst": CatalystType.EARNINGS,
        "catalyst_headline": "Company reports earnings",
        "as_of": datetime(2026, 7, 20, 8, 0, tzinfo=UTC),
    }
    values.update(overrides)
    return ReferenceRecord(**values)


def crypto_record(
    **overrides: object,
) -> ReferenceRecord:
    values: dict[str, object] = {
        "symbol": "BTCUSD",
        "asset_class": AssetClass.CRYPTO,
        "exchange": "CRYPTO",
        "previous_close": D("95000"),
        "average_30_day_volume": D("25000"),
        "float_shares": None,
        "market_cap": D("1900000000000"),
        "shares_outstanding": D("20000000"),
        "tradable": True,
        "catalyst": CatalystType.NONE,
        "catalyst_headline": None,
        "as_of": datetime(2026, 7, 20, 8, 0, tzinfo=UTC),
    }
    values.update(overrides)
    return ReferenceRecord(**values)


def test_service_returns_stock_reference_data() -> None:
    provider = InMemoryReferenceDataProvider(
        (stock_record(),)
    )
    service = ReferenceDataService(provider)

    record = service.get(" test ")

    assert record.symbol == "TEST"
    assert record.asset_class is AssetClass.STOCK
    assert record.exchange == "NASDAQ"
    assert record.float_shares == D("8000000")


def test_service_supports_crypto_reference_data() -> None:
    provider = InMemoryReferenceDataProvider(
        (crypto_record(),)
    )
    service = ReferenceDataService(provider)

    record = service.get(
        "btcusd",
        AssetClass.CRYPTO,
    )

    assert record.symbol == "BTCUSD"
    assert record.asset_class is AssetClass.CRYPTO
    assert record.float_shares is None


def test_service_uses_cached_record() -> None:
    first = stock_record(previous_close=D("4"))
    provider = InMemoryReferenceDataProvider((first,))
    service = ReferenceDataService(provider)

    initial = service.get("TEST")

    provider.put(
        stock_record(previous_close=D("5"))
    )

    cached = service.get("TEST")

    assert initial.previous_close == D("4")
    assert cached.previous_close == D("4")


def test_force_refresh_bypasses_cache() -> None:
    provider = InMemoryReferenceDataProvider(
        (stock_record(previous_close=D("4")),)
    )
    service = ReferenceDataService(provider)

    service.get("TEST")

    provider.put(
        stock_record(previous_close=D("5"))
    )

    refreshed = service.refresh("TEST")

    assert refreshed.previous_close == D("5")


def test_expired_cache_fetches_new_record() -> None:
    current_time = datetime(
        2026,
        7,
        20,
        8,
        0,
        tzinfo=UTC,
    )

    def clock() -> datetime:
        return current_time

    cache = ReferenceDataCache(clock=clock)
    policy = ReferenceDataPolicy(
        stock_ttl=timedelta(minutes=5),
        crypto_ttl=timedelta(minutes=1),
    )
    provider = InMemoryReferenceDataProvider(
        (stock_record(previous_close=D("4")),)
    )
    service = ReferenceDataService(
        provider,
        cache=cache,
        policy=policy,
    )

    first = service.get("TEST")
    assert first.previous_close == D("4")

    provider.put(
        stock_record(previous_close=D("5"))
    )

    current_time += timedelta(minutes=6)

    second = service.get("TEST")

    assert second.previous_close == D("5")


def test_composite_provider_uses_fallback() -> None:
    empty_provider = InMemoryReferenceDataProvider()
    fallback_provider = InMemoryReferenceDataProvider(
        (stock_record(),)
    )

    provider = CompositeReferenceDataProvider(
        (
            empty_provider,
            fallback_provider,
        )
    )

    record = provider.get_reference_data(
        "TEST",
        AssetClass.STOCK,
    )

    assert record.symbol == "TEST"


def test_missing_symbol_raises_clear_error() -> None:
    provider = InMemoryReferenceDataProvider()
    service = ReferenceDataService(provider)

    with pytest.raises(ReferenceDataNotFoundError):
        service.get("MISSING")


def test_reference_record_normalizes_text() -> None:
    record = stock_record(
        symbol=" test ",
        exchange=" nasdaq ",
        catalyst_headline="  Strong earnings report  ",
    )

    assert record.symbol == "TEST"
    assert record.exchange == "NASDAQ"
    assert (
        record.catalyst_headline
        == "Strong earnings report"
    )


def test_reference_record_rejects_naive_timestamp() -> None:
    with pytest.raises(
        ValueError,
        match="as_of must be timezone-aware",
    ):
        stock_record(
            as_of=datetime(2026, 7, 20, 8, 0)
        )
