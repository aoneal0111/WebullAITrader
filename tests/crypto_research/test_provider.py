from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.crypto_research import (
    CryptoPair,
    CryptoProviderError,
    MalformedCryptoQuoteError,
    WebullCryptoResearchProvider,
    observation_from_webull,
    pair_from_webull,
)
from app.configuration import (
    MarketDataConfiguration,
    TradingConfiguration,
    TradingEnvironment,
)
from app.webull.request_audit import AuditedMarketDataClient, RequestIsolationGuard


class Response:
    def __init__(self, value, status_code=200):
        self.value, self.status_code = value, status_code

    def json(self):
        return self.value


class InstrumentApi:
    def __init__(self, rows, failures=0):
        self.rows, self.failures, self.calls = rows, failures, []

    def get_crypto_instrument(self, **kwargs):
        self.calls.append(kwargs)
        if self.failures:
            self.failures -= 1
            raise OSError("temporary")
        return Response(self.rows)


class MarketApi:
    def __init__(self, rows):
        self.rows, self.calls, self.history_calls = rows, [], []

    def get_crypto_snapshot(self, symbols, category):
        self.calls.append((symbols, category))
        return Response(self.rows)

    def get_crypto_history_bar(self, *args):
        self.history_calls.append(args)
        return Response([{"timestamp": 1, "close": "100"}])


class Client:
    def __init__(self, instruments, snapshots, failures=0):
        self.instrument = InstrumentApi(instruments, failures)
        self.crypto_market_data = MarketApi(snapshots)


class Lazy:
    def __init__(self, client):
        self.client, self.gets = client, 0

    def get(self):
        self.gets += 1
        return self.client


def instrument(symbol="BTCUSD", base="BTC", quote="USD"):
    return {
        "category": "US_CRYPTO", "currency": quote,
        "exchange_code": "CCC", "instrument_id": "1", "lot_size": "0.0001",
        "max_trade_amt": "1000000", "max_trade_qty": "100",
        "min_trade_amt": "1", "min_trade_qty": "0.0001",
        "name": base, "price_step": "0.01", "status": "OC",
        "symbol": symbol,
    }


def snapshot(symbol="BTCUSD"):
    return {
        "symbol": symbol, "price": "60000", "bid": "59999",
        "ask": "60001", "ask_size": "0.2", "bid_size": "0.1",
        "change": "100", "change_ratio": "0.1", "close": "60000",
        "high": "61000", "instrument_id": "1", "last_trade_time": 1788782400000,
        "low": "58000", "open": "59000", "pre_close": "59900",
        "quote_time": 1788782400000,
    }


def test_pair_normalization_requires_provider_metadata_for_compact_symbols() -> None:
    assert pair_from_webull(instrument()).canonical_symbol == "BTC/USD"
    assert pair_from_webull({"symbol": "XBT-USD"}).canonical_symbol == "XBT/USD"
    with pytest.raises(ValueError, match="metadata"):
        pair_from_webull({"symbol": "BTCUSD"})


def test_provider_is_lazy_bounded_and_uses_only_crypto_namespaces() -> None:
    client = Client([instrument()], [snapshot()])
    lazy = Lazy(client)
    provider = WebullCryptoResearchProvider(
        lazy, minimum_request_interval_seconds=0, retry_attempts=0,
        clock=lambda: datetime(2026, 9, 7, 12, tzinfo=UTC),
    )
    assert lazy.gets == 0
    pairs = provider.discover((CryptoPair.configured("BTC/USD"),))
    observations = provider.snapshots(pairs)
    assert lazy.gets == 2
    assert pairs[0].canonical_symbol == "BTC/USD"
    assert observations[0].pair.asset_type.value == "CRYPTO"
    assert observations[0].volume is None
    assert client.instrument.calls[0]["category"] == "US_CRYPTO"
    assert "status" not in client.instrument.calls[0]
    assert client.crypto_market_data.calls == [(["BTCUSD"], "US_CRYPTO")]


def test_real_instrument_list_is_bounded_and_normalizes_symbol_plus_currency() -> None:
    rows = [instrument(f"C{index:03d}USD", base=f"C{index:03d}") for index in range(342)]
    assert len(tuple(pair_from_webull(row) for row in rows)) == 342
    client = Client(rows, [])
    provider = WebullCryptoResearchProvider(
        client, minimum_request_interval_seconds=0, retry_attempts=0,
    )

    pairs = provider.discover()

    assert len(pairs) == 256
    assert pairs[0].quote_asset == "USD"
    assert "status" not in client.instrument.calls[0]
    assert "LISTING" not in client.instrument.calls[0].values()


def test_real_snapshot_without_volume_preserves_quote_and_unavailable_volume() -> None:
    pair = pair_from_webull(instrument())
    item = observation_from_webull(
        pair, snapshot(), datetime(2026, 9, 7, 12, tzinfo=UTC)
    )

    assert pair.canonical_symbol == "BTC/USD"
    assert item.price == Decimal("60000")
    assert item.bid == Decimal("59999")
    assert item.ask == Decimal("60001")
    assert item.volume is None
    assert item.timestamp == datetime(2026, 9, 7, 12, tzinfo=UTC)


def test_provider_retry_and_historical_bounds_are_explicit() -> None:
    client = Client([instrument()], [snapshot()], failures=1)
    provider = WebullCryptoResearchProvider(
        client, minimum_request_interval_seconds=0, retry_attempts=1,
    )
    pair = provider.discover()[0]
    assert len(client.instrument.calls) == 2
    assert provider.historical_bars(pair, count=1200)
    with pytest.raises(ValueError, match="1..1200"):
        provider.historical_bars(pair, count=1201)


def test_malformed_quote_and_provider_failure_do_not_return_partial_objects() -> None:
    pair = CryptoPair.configured("BTC/USD")
    malformed = Client([instrument()], [{"symbol": "BTCUSD", "price": "bad", "volume": "1"}])
    provider = WebullCryptoResearchProvider(
        malformed, minimum_request_interval_seconds=0, retry_attempts=0,
    )
    with pytest.raises(CryptoProviderError):
        provider.snapshots((pair,))
    with pytest.raises(MalformedCryptoQuoteError):
        # The normalized cause remains available for direct parser diagnostics.
        from app.crypto_research.provider import observation_from_webull
        observation_from_webull(pair, malformed.crypto_market_data.rows[0], datetime.now(UTC))


def test_unsupported_symbol_is_explicit_and_contained_at_provider_boundary() -> None:
    class UnsupportedMarket(MarketApi):
        def get_crypto_snapshot(self, symbols, category):
            return Response({"error_code": "UNSUPPORTED_SYMBOL"}, status_code=417)

    client = Client([instrument()], [])
    client.crypto_market_data = UnsupportedMarket([])
    provider = WebullCryptoResearchProvider(
        client, minimum_request_interval_seconds=0, retry_attempts=0,
    )
    with pytest.raises(CryptoProviderError, match="rejected"):
        provider.snapshots((CryptoPair.configured("BTC/USD"),))


def test_crypto_namespace_uses_market_data_identity_only() -> None:
    trading = TradingConfiguration(
        TradingEnvironment.TEST,
        "paper-account",
        "trade-key",
        "trade-secret",
        "https://api.sandbox.webull.com",
        "wss://data-api.sandbox.webull.com/mqtt",
    )
    market = MarketDataConfiguration(
        TradingEnvironment.PRODUCTION,
        "data-key",
        "data-secret",
        "https://api.webull.com",
        "wss://data-api.webull.com/mqtt",
    )
    guard = RequestIsolationGuard(trading, market)
    client = AuditedMarketDataClient(
        Client([instrument()], [snapshot()]), guard, market
    )

    client.crypto_market_data.get_crypto_snapshot(["BTCUSD"], "US_CRYPTO")

    assert [record.capability_result for record in guard.records] == [
        "REQUESTED",
        "SUCCEEDED",
    ]
    assert all(record.service == "MARKET_DATA" for record in guard.records)
    assert all(record.environment == "PRODUCTION" for record in guard.records)
    assert all("crypto_market_data" in record.endpoint for record in guard.records)
