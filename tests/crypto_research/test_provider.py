from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.crypto_research import (
    CryptoPair,
    CryptoProviderError,
    MalformedCryptoQuoteError,
    WebullCryptoResearchProvider,
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
        "symbol": symbol, "base_currency": base, "currency_code": quote,
        "instrument_id": "1", "status": "LISTING",
    }


def snapshot(symbol="BTCUSD"):
    return {
        "symbol": symbol, "price": "60000", "bid": "59999",
        "ask": "60001", "volume": "100", "high": "61000",
        "low": "58000", "quote_time": 1788782400000,
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
    assert client.instrument.calls[0]["category"] == "US_CRYPTO"
    assert client.crypto_market_data.calls == [(["BTCUSD"], "US_CRYPTO")]


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
