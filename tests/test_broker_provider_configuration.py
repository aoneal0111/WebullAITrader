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


def test_allowed_symbols_do_not_become_implicit_market_data_symbols():
    configuration = load_configuration({"ALLOWED_SYMBOLS": "AAPL"})

    assert configuration.allowed_symbols == ("AAPL",)
    assert configuration.market_data_symbols == ()


def test_warrior_forward_paper_is_explicit_and_never_a_live_flag(tmp_path):
    default = load_configuration({})
    enabled = load_configuration({
        "WARRIOR_FORWARD_PAPER_ENABLED": "true",
        "WARRIOR_FORWARD_CAPTURE_PATH": str(tmp_path / "forward.sqlite3"),
    })
    assert default.warrior_forward_paper_enabled is False
    assert enabled.warrior_forward_paper_enabled is True
    assert enabled.warrior_forward_capture_path == (tmp_path / "forward.sqlite3").resolve()
    assert enabled.live_trading_enabled is False
