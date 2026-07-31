from types import SimpleNamespace

from app.configuration import MarketDataConfiguration, TradingEnvironment
from app.webull.market_data_probe import (
    MarketDataCapabilityProbe,
    ProbeState,
)
from app.webull.sdk_market_data import LazyOfficialDataClient
from app.webull.sdk_market_data import EnvironmentSupportCache


class Response:
    status_code = 200

    def json(self):
        return {"data": [{"symbol": "AAPL"}]}


class RestClient:
    def __init__(self, *, bars_error=None, quote_error=None):
        self.market_data = SimpleNamespace(
            get_history_bar=lambda *args, **kwargs: (
                _raise(bars_error) if bars_error else Response()
            ),
            get_quotes=lambda *args, **kwargs: (
                _raise(quote_error) if quote_error else Response()
            ),
            get_snapshot=lambda *args, **kwargs: Response(),
        )
        self.instrument = SimpleNamespace(
            get_instrument=lambda *args, **kwargs: Response()
        )


class Stream:
    def __init__(self):
        self.calls = []

    def connect(self):
        self.calls.append("connect")

    def subscribe(self, symbols):
        self.calls.append(("subscribe", tuple(symbols)))


class Unsupported(RuntimeError):
    error_code = "UNSUPPORTED_SYMBOL"
    http_status = 417


def _raise(error):
    raise error


def configuration(
    key="data-key", secret="data-secret", environment=TradingEnvironment.LIVE
):
    return MarketDataConfiguration(
        environment,
        key,
        secret,
        "https://data.example",
        "wss://stream.example/mqtt",
    )


def test_probe_validates_rest_stream_subscription_and_entitlement():
    stream = Stream()
    result = MarketDataCapabilityProbe(
        configuration(), LazyOfficialDataClient(RestClient), stream
    ).run()

    assert result.scanner_ready is True
    assert result.bars.state is ProbeState.AVAILABLE
    assert result.quotes.state is ProbeState.AVAILABLE
    assert result.snapshots.state is ProbeState.AVAILABLE
    assert result.reference.state is ProbeState.AVAILABLE
    assert result.streaming.state is ProbeState.AVAILABLE
    assert result.subscription.state is ProbeState.AVAILABLE
    assert result.entitlement.state is ProbeState.AVAILABLE
    assert stream.calls == ["connect", ("subscribe", ("AAPL", "SPY", "TSLA"))]
    assert result.credential_fingerprint.startswith("fp_")
    assert "data-key" not in result.credential_fingerprint


def test_all_unsupported_bars_differs_from_missing_entitlement():
    unsupported = MarketDataCapabilityProbe(
        configuration(environment=TradingEnvironment.TEST),
        LazyOfficialDataClient(lambda: RestClient(bars_error=Unsupported())),
        Stream(),
    ).run()
    denied = MarketDataCapabilityProbe(
        configuration(),
        LazyOfficialDataClient(
            lambda: RestClient(quote_error=PermissionError("403 secret header"))
        ),
        Stream(),
    ).run()

    assert unsupported.bars.state is ProbeState.UNSUPPORTED
    assert "Sandbox market-data catalog" in unsupported.reason
    assert denied.entitlement.state is ProbeState.NOT_ENTITLED
    assert denied.reason == "Production market-data entitlement is not granted."
    assert "secret header" not in denied.quotes.detail


def test_missing_credentials_disables_probe_without_constructing_clients():
    result = MarketDataCapabilityProbe(
        configuration("", ""),
        LazyOfficialDataClient(lambda: (_ for _ in ()).throw(AssertionError())),
        Stream(),
    ).run()
    assert result.credentials.state is ProbeState.CREDENTIALS_MISSING
    assert result.reason == "Production market-data credentials are missing."


def test_support_cache_isolated_by_environment_credentials_and_asset_category():
    cache = EnvironmentSupportCache()
    cache.put(
        "TEST", "US_STOCK", "AAPL", False, credential_scope="fp_sandbox"
    )

    assert cache.get(
        "TEST", "US_STOCK", "AAPL", credential_scope="fp_sandbox"
    ) is False
    assert cache.get(
        "LIVE", "US_STOCK", "AAPL", credential_scope="fp_sandbox"
    ) is None
    assert cache.get(
        "TEST", "US_STOCK", "AAPL", credential_scope="fp_production"
    ) is None
    assert cache.get(
        "TEST", "US_OPTION", "AAPL", credential_scope="fp_sandbox"
    ) is None
