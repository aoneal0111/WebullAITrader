from datetime import UTC, datetime
from inspect import signature
import logging
from types import SimpleNamespace

from app.configuration import MarketDataConfiguration, TradingEnvironment
from app.webull.market_data_probe import (
    MarketDataCapabilityProbe,
    ProbeState,
    SymbolProbeState,
)
from app.webull.market_data_session import MarketDataSession
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
    def __init__(self, *, acknowledged=True, reconnect_ready=True):
        self.calls = []
        self.heartbeat_ok = True
        self.subscription_acknowledged = acknowledged
        self.reconnect_ready = reconnect_ready

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
    assert result.heartbeat.state is ProbeState.AVAILABLE
    assert result.reconnect.state is ProbeState.AVAILABLE
    assert [item.result for item in result.symbol_results] == [
        SymbolProbeState.SUPPORTED,
        SymbolProbeState.SUPPORTED,
        SymbolProbeState.SUPPORTED,
        SymbolProbeState.SUPPORTED,
        SymbolProbeState.SUPPORTED,
    ]
    assert stream.calls == [
        "connect",
        ("subscribe", ("AAPL",)),
        ("subscribe", ("SPY",)),
        ("subscribe", ("TSLA",)),
        ("subscribe", ("MSFT",)),
        ("subscribe", ("NVDA",)),
    ]
    assert result.credential_fingerprint.startswith("fp_")
    assert "data-key" not in result.credential_fingerprint


def test_probe_sanitizes_exception_text_by_default(monkeypatch, caplog):
    monkeypatch.delenv("ATLAS_DEBUG_MARKET_DATA_PROBE", raising=False)
    caplog.set_level(logging.ERROR)

    result = MarketDataCapabilityProbe(
        configuration(),
        LazyOfficialDataClient(
            lambda: _raise(RuntimeError("diagnostic connection detail"))
        ),
        Stream(),
    ).run()

    assert result.endpoint.detail == "RuntimeError"
    assert "diagnostic connection detail" not in caplog.text


def test_debug_probe_includes_exception_text_and_traceback(monkeypatch, caplog):
    monkeypatch.setenv("ATLAS_DEBUG_MARKET_DATA_PROBE", "true")
    caplog.set_level(logging.ERROR)

    result = MarketDataCapabilityProbe(
        configuration(),
        LazyOfficialDataClient(
            lambda: _raise(RuntimeError("diagnostic connection detail"))
        ),
        Stream(),
    ).run()

    assert result.endpoint.detail == "RuntimeError: diagnostic connection detail"
    assert "Traceback (most recent call last)" in caplog.text
    assert "RuntimeError: diagnostic connection detail" in caplog.text


def test_debug_probe_redacts_credentials_from_detail_and_logs(monkeypatch, caplog):
    monkeypatch.setenv("ATLAS_DEBUG_MARKET_DATA_PROBE", "true")
    caplog.set_level(logging.ERROR)
    exposed = (
        "api_key=data-key api_secret=data-secret; "
        "Authorization: Bearer oauth-token; signature=request-signature; "
        "https://user:password@data.example/bars?access_token=url-token&sig=url-signature"
    )

    result = MarketDataCapabilityProbe(
        configuration(),
        LazyOfficialDataClient(lambda: _raise(RuntimeError(exposed))),
        Stream(),
    ).run()
    output = result.endpoint.detail + caplog.text

    for credential in (
        "data-key",
        "data-secret",
        "oauth-token",
        "request-signature",
        "url-token",
        "url-signature",
        "user:password",
    ):
        assert credential not in output
    assert "[REDACTED]" in result.endpoint.detail
    assert "https://data.example/bars" in result.endpoint.detail


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
    assert unsupported.reason == "NO_SUPPORTED_SYMBOLS"
    assert denied.entitlement.state is ProbeState.NOT_ENTITLED
    assert denied.reason == "Production market-data entitlement is not granted."
    assert "secret header" not in denied.quotes.detail
    assert all(
        item.result is SymbolProbeState.UNSUPPORTED
        for item in unsupported.symbol_results
    )
    assert all(
        item.result is SymbolProbeState.NO_ENTITLEMENT
        for item in denied.symbol_results
    )


def test_connected_stream_requires_subscription_acknowledgement():
    result = MarketDataCapabilityProbe(
        configuration(), LazyOfficialDataClient(RestClient),
        Stream(acknowledged=False),
    ).run()

    assert result.streaming.state is ProbeState.AVAILABLE
    assert result.subscription.state is ProbeState.UNAVAILABLE
    assert result.scanner_ready is False
    assert result.reason == "STREAM_CONNECTED_SUBSCRIPTION_DENIED"
    assert all(
        item.result is SymbolProbeState.UNKNOWN
        for item in result.symbol_results
    )


def test_entitlement_failure_takes_precedence_over_unsupported_symbols():
    class MixedRestClient:
        def __init__(self):
            self.market_data = SimpleNamespace(
                get_history_bar=lambda *args, **kwargs: _raise(Unsupported()),
                get_quotes=lambda symbol, *args, **kwargs: (
                    _raise(PermissionError("403"))
                    if symbol == "AAPL" else Response()
                ),
                get_snapshot=lambda *args, **kwargs: Response(),
            )
            self.instrument = SimpleNamespace(
                get_instrument=lambda *args, **kwargs: Response()
            )

    result = MarketDataCapabilityProbe(
        configuration(environment=TradingEnvironment.TEST),
        LazyOfficialDataClient(MixedRestClient),
        Stream(),
    ).run()
    assert result.reason == "Sandbox market-data entitlement is not granted."
    assert all(
        item.result is SymbolProbeState.NO_ENTITLEMENT
        for item in result.symbol_results
    )


def test_reconnect_capability_is_a_scanner_prerequisite():
    result = MarketDataCapabilityProbe(
        configuration(), LazyOfficialDataClient(RestClient),
        Stream(reconnect_ready=False),
    ).run()
    assert result.reconnect.state is ProbeState.UNAVAILABLE
    assert result.scanner_ready is False


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
        "TEST", "US_STOCK", "AAPL", False, identity_scope="fp_sandbox"
    )

    assert cache.get(
        "TEST", "US_STOCK", "AAPL", identity_scope="fp_sandbox"
    ) is False
    assert cache.get(
        "LIVE", "US_STOCK", "AAPL", identity_scope="fp_sandbox"
    ) is None
    assert cache.get(
        "TEST", "US_STOCK", "AAPL", identity_scope="fp_production"
    ) is None
    assert cache.get(
        "TEST", "US_OPTION", "AAPL", identity_scope="fp_sandbox"
    ) is None


def test_probe_uses_supported_daily_timespan_and_sdk_snapshot_keywords():
    calls = []

    class ExactMarketData:
        def get_history_bar(
            self, symbol, category, timespan, *, count, real_time_required
        ):
            calls.append(("bars", symbol, category, timespan, count))
            return Response()

        def get_quotes(self, symbol, category):
            return Response()

        def get_snapshot(self, *, symbols, category):
            calls.append(("snapshot", symbols, category))
            return Response()

    client = SimpleNamespace(
        market_data=ExactMarketData(),
        instrument=SimpleNamespace(get_instrument=lambda **kwargs: Response()),
    )
    result = MarketDataCapabilityProbe(
        configuration(environment=TradingEnvironment.TEST),
        LazyOfficialDataClient(lambda: client),
        Stream(),
    ).run()

    assert result.scanner_ready is True
    assert calls[0] == ("bars", "AAPL", "US_STOCK", "D", "1")
    assert calls[1] == ("snapshot", ["AAPL"], "US_STOCK")
    assert all(call[3] == "D" for call in calls if call[0] == "bars")


def test_snapshot_parameter_names_match_installed_webull_sdk():
    from webull.data.quotes.market_data import MarketData

    parameters = tuple(signature(MarketData.get_snapshot).parameters)

    assert parameters[:3] == ("self", "symbols", "category")


def test_sandbox_requires_aapl_but_records_optional_unsupported_symbols():
    class SandboxMarketData:
        @staticmethod
        def _response(symbol):
            return Response() if symbol == "AAPL" else _raise(Unsupported())

        def get_history_bar(self, symbol, *args, **kwargs):
            return self._response(symbol)

        def get_quotes(self, symbol, *args, **kwargs):
            return self._response(symbol)

        def get_snapshot(self, *, symbols, category):
            return self._response(symbols[0])

    client = SimpleNamespace(
        market_data=SandboxMarketData(),
        instrument=SimpleNamespace(
            get_instrument=lambda *, symbols, **kwargs: (
                Response() if symbols == "AAPL" else _raise(Unsupported())
            )
        ),
    )
    sandbox = MarketDataCapabilityProbe(
        configuration(environment=TradingEnvironment.TEST),
        LazyOfficialDataClient(lambda: client),
        Stream(),
    ).run()
    production = MarketDataCapabilityProbe(
        configuration(environment=TradingEnvironment.LIVE),
        LazyOfficialDataClient(lambda: client),
        Stream(),
    ).run()

    assert sandbox.scanner_ready is True
    assert sandbox.probe_symbol_supported is True
    assert sandbox.symbol_results[0].result is SymbolProbeState.SUPPORTED
    assert all(
        item.result is SymbolProbeState.UNSUPPORTED
        for item in sandbox.symbol_results[1:]
    )
    assert production.scanner_ready is False


def test_sandbox_unusable_aapl_reports_no_supported_symbols():
    result = MarketDataCapabilityProbe(
        configuration(environment=TradingEnvironment.TEST),
        LazyOfficialDataClient(lambda: RestClient(bars_error=Unsupported())),
        Stream(),
    ).run()

    assert result.probe_symbol_supported is False
    assert result.reason == "NO_SUPPORTED_SYMBOLS"


def test_malformed_snapshot_is_a_clear_capability_failure():
    malformed = SimpleNamespace(status_code=200, json=lambda: {"unexpected": {}})
    client = RestClient()
    client.market_data.get_snapshot = lambda **kwargs: malformed

    result = MarketDataCapabilityProbe(
        configuration(environment=TradingEnvironment.TEST),
        LazyOfficialDataClient(lambda: client),
        Stream(),
    ).run()

    assert result.snapshots.state is ProbeState.UNAVAILABLE
    assert result.symbol_results[0].snapshot.detail == "MALFORMED_RESPONSE"


class SessionEntitlementStream(Stream):
    def __init__(self, *, overnight_required):
        super().__init__()
        self.overnight_required = overnight_required

    def subscribe(self, symbols):
        if self.overnight_required:
            raise PermissionError("403 OVERNIGHT permission signature=secret")
        super().subscribe(symbols)


def test_regular_session_needs_no_overnight_entitlement():
    result = MarketDataCapabilityProbe(
        configuration(environment=TradingEnvironment.TEST),
        LazyOfficialDataClient(RestClient),
        SessionEntitlementStream(overnight_required=False),
        clock=lambda: datetime(2026, 7, 30, 15, tzinfo=UTC),
    ).run()

    assert result.current_session is MarketDataSession.REGULAR
    assert result.current_session_entitled is True
    assert result.scanner_ready is True


def test_overnight_denial_is_explicit_and_disables_scanner_capability():
    result = MarketDataCapabilityProbe(
        configuration(environment=TradingEnvironment.TEST),
        LazyOfficialDataClient(RestClient),
        SessionEntitlementStream(overnight_required=True),
        clock=lambda: datetime(2026, 7, 31, 1, tzinfo=UTC),
    ).run()

    assert result.current_session is MarketDataSession.OVERNIGHT
    assert result.current_session_entitled is False
    assert result.scanner_ready is False
    assert result.reason == "OVERNIGHT_ENTITLEMENT_REQUIRED"
    assert "secret" not in result.subscription.detail
