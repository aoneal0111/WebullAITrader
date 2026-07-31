from decimal import Decimal

import pytest

from app.configuration import (
    MarketDataConfiguration,
    TradingConfiguration,
    TradingEnvironment,
    load_configuration,
)
from app.webull.client_factories import MarketDataClientFactory, TradingClientFactory


def test_factories_create_distinct_clients_without_crossing_credentials():
    calls = []
    trading_client = object()
    market_data_client = object()

    def trade_builder(**kwargs):
        calls.append(("trade", kwargs))
        return trading_client

    def data_builder(**kwargs):
        calls.append(("data", kwargs))
        return market_data_client

    trading = TradingConfiguration(
        TradingEnvironment.TEST, "account", "trade-key", "trade-secret",
        "https://trade.test.example", "wss://trade.test.example/mqtt",
    )
    market_data = MarketDataConfiguration(
        TradingEnvironment.LIVE, "data-key", "data-secret",
        "https://data.example", "wss://stream.data.example/mqtt",
    )

    assert TradingClientFactory(trading, trade_builder).create() is trading_client
    assert MarketDataClientFactory(market_data, data_builder).create() is market_data_client
    assert trading_client is not market_data_client
    assert calls[0] == ("trade", {
        "app_key": "trade-key", "app_secret": "trade-secret",
        "endpoint": "https://trade.test.example",
        "timeout_seconds": Decimal("10"),
    })
    assert calls[1] == ("data", {
        "app_key": "data-key", "app_secret": "data-secret",
        "endpoint": "https://data.example",
    })


def test_factory_rejects_the_other_configuration_type():
    market_data = MarketDataConfiguration(
        TradingEnvironment.LIVE, "key", "secret", "https://data", "wss://data"
    )
    with pytest.raises(TypeError, match="trading configuration is required"):
        TradingClientFactory(market_data, lambda **kwargs: object()).create()


def test_factories_do_not_share_authentication_or_tokens():
    class Client:
        def __init__(self, token):
            self.token = token

    trading = TradingConfiguration(
        TradingEnvironment.TEST, "account", "tk", "ts",
        "https://trade.example", "wss://trade.example/mqtt",
    )
    market_data = MarketDataConfiguration(
        TradingEnvironment.PRODUCTION, "mk", "ms",
        "https://data.example", "wss://data.example/mqtt",
    )
    trade_client = TradingClientFactory(
        trading, lambda **kwargs: Client("trade-token")
    ).create()
    data_client = MarketDataClientFactory(
        market_data, lambda **kwargs: Client("data-token")
    ).create()

    assert trade_client.token == "trade-token"
    assert data_client.token == "data-token"
    assert trade_client.__dict__ is not data_client.__dict__


def test_scoped_configuration_overrides_legacy_without_crossing():
    configuration = load_configuration({
        "TRADING_ENVIRONMENT": "TEST",
        "MARKET_DATA_ENVIRONMENT": "LIVE",
        "WEBULL_API_KEY": "legacy-key",
        "WEBULL_API_SECRET": "legacy-secret",
        "WEBULL_API_BASE_URL": "https://legacy.example",
        "WEBULL_STREAM_URL": "wss://legacy.example/mqtt",
        "WEBULL_ACCOUNT_ID": "legacy-account",
        "WEBULL_MARKET_DATA_APP_KEY": "data-key",
        "WEBULL_MARKET_DATA_APP_SECRET": "data-secret",
        "WEBULL_MARKET_DATA_API_BASE_URL": "https://data.example",
        "WEBULL_MARKET_DATA_STREAM_URL": "wss://stream.data.example/mqtt",
    })

    assert configuration.trading.api_key == "legacy-key"
    assert configuration.trading.account_id == "legacy-account"
    assert configuration.market_data.api_key == "data-key"
    assert configuration.market_data.environment is TradingEnvironment.LIVE


def test_partial_scoped_configuration_is_rejected_as_ambiguous():
    with pytest.raises(ValueError, match="ambiguous mixed market-data"):
        load_configuration({"WEBULL_MARKET_DATA_APP_KEY": "data-key"})


def test_legacy_configuration_populates_both_compatibility_sections():
    configuration = load_configuration({
        "WEBULL_API_KEY": "legacy-key",
        "WEBULL_API_SECRET": "legacy-secret",
        "WEBULL_API_BASE_URL": "https://legacy.example",
        "WEBULL_STREAM_URL": "wss://legacy.example/mqtt",
        "WEBULL_ACCOUNT_ID": "legacy-account",
    })
    assert configuration.trading.api_key == "legacy-key"
    assert configuration.market_data.api_key == "legacy-key"
