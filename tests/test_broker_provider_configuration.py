from app.configuration.loader import load_configuration


def test_default_provider_is_webull():
    configuration = load_configuration({})

    assert configuration.broker_provider == "webull"


def test_provider_is_case_normalized():
    configuration = load_configuration(
        {
            "BROKER_PROVIDER": "WEBULL",
        }
    )

    assert configuration.broker_provider == "webull"


def test_market_data_configuration_uses_explicit_symbols_and_reconnect():
    configuration = load_configuration(
        {
            "MARKET_DATA_STREAMING_ENABLED": "true",
            "MARKET_DATA_SYMBOLS": "msft,aapl,MSFT",
            "STREAM_RECONNECT_ATTEMPTS": "7",
            "STREAM_RECONNECT_BACKOFF_SECONDS": "2.5",
        }
    )

    assert configuration.market_data_streaming_enabled is True
    assert configuration.market_data_symbols == ("AAPL", "MSFT")
    assert configuration.stream_reconnect_attempts == 7
    assert str(configuration.stream_reconnect_backoff_seconds) == "2.5"
