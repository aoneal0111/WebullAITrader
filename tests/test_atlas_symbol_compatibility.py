from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.live_scanner.coordinator import LiveScannerCoordinator
from app.momentum_scanner import AssetClass
from app.realtime_scanner import RealtimeScannerEngine
from app.reference_data import ReferenceDataService
from app.universe import UniverseService
from app.webull.sdk_market_data import (
    EnvironmentSupportCache,
    LazyOfficialDataClient,
    WebullScannerReferenceProvider,
    WebullScannerUniverseProvider,
    configure_official_sdk_logging,
)
from app.webull.websocket_client import OfficialSdkStreamBackend
from app.webull.logging import StructuredLogger


NOW = datetime(2026, 7, 30, 15, 0, tzinfo=UTC)


class Response:
    status_code = 200

    def __init__(self, value):
        self._value = value

    def json(self):
        return self._value


class UnsupportedSymbol(Exception):
    error_code = "UNSUPPORTED_SYMBOL"
    http_status = 417


class Screener:
    def get_gainers_losers(self, *args, **kwargs):
        return Response(
            {
                "data": [
                    {
                        "instrument_id": "913256135",
                        "symbol": "GOOD",
                        "exchange_code": "NMS",
                        "currency_code": "USD",
                        "price": "5",
                        "pre_close": "4",
                        "volume": "7000000",
                        "relative_volume_10d": "7",
                        "market_value": "50000000",
                    },
                    {
                        "instrument_id": "913256136",
                        "symbol": "BAD",
                        "exchange_code": "NYSE",
                        "currency_code": "USD",
                        "price": "6",
                        "pre_close": "5",
                        "volume": "6000000",
                        "relative_volume_10d": "6",
                        "market_value": "60000000",
                    },
                ]
            }
        )

    def get_most_active(self, *args, **kwargs):
        return Response({"data": []})


class Instrument:
    def get_instrument(self, *, symbols, category, page_size):
        return Response(
            [
                {
                    "instrument_id": (
                        "913256135" if symbol == "GOOD" else "913256136"
                    ),
                    "symbol": symbol,
                    "exchange_code": "NMS" if symbol == "GOOD" else "NYSE",
                    "category": category,
                    "status": "OC",
                    "fractionable": False,
                    "marginable": True,
                    "shortable": False,
                }
                for symbol in symbols.split(",")
            ]
        )


class Fundamentals:
    def get_earnings_calendar(self, symbol, category):
        return Response([])

    def get_sec_filings(self, symbol, category):
        return Response([])


class MarketData:
    def __init__(self, behavior=None):
        self.behavior = behavior or {}
        self.calls = []

    def get_history_bar(
        self,
        symbol,
        category,
        timespan,
        *,
        count,
        real_time_required,
    ):
        self.calls.append((symbol, category, timespan, count))
        action = self.behavior.get(symbol)
        if isinstance(action, list):
            action = action.pop(0)
        if isinstance(action, Exception):
            raise action
        if action == "unsupported":
            raise UnsupportedSymbol("input symbol invalid")
        return Response([{"volume": "1000000"}, {"volume": "3000000"}])


class Pipeline:
    def consume(self, event):
        return None


class Transport:
    def __init__(self):
        self.calls = []

    def connect(self):
        self.calls.append("connect")

    def disconnect(self):
        self.calls.append("disconnect")

    def subscribe(self, symbols):
        self.calls.append(("subscribe", symbols))

    def read_event(self):
        return None


def build(environment="SANDBOX", behavior=None, cache=None):
    market_data = MarketData(behavior)
    client = SimpleNamespace(
        screener=Screener(),
        instrument=Instrument(),
        fundamentals=Fundamentals(),
        market_data=market_data,
    )
    lazy = LazyOfficialDataClient(lambda: client)
    universe_provider = WebullScannerUniverseProvider(lazy, clock=lambda: NOW)
    reference_provider = WebullScannerReferenceProvider(
        lazy,
        universe_provider,
        clock=lambda: NOW,
        environment=environment,
        support_cache=cache,
        event_sink=lambda event: events.append(event),
    )
    engine = RealtimeScannerEngine(
        UniverseService(universe_provider),
        ReferenceDataService(reference_provider),
        Pipeline(),
        clock=lambda: NOW,
    )
    return engine, universe_provider, market_data


events = []


def test_screener_preserves_canonical_api_identity_and_raw_boundaries():
    engine, universe, _ = build()
    symbols = universe.list_symbols(AssetClass.STOCK)

    assert symbols[0].display_symbol == "BAD"
    good = next(item for item in symbols if item.symbol == "GOOD")
    assert good.api_symbol == "GOOD"
    assert good.instrument_id == "913256135"
    assert good.exchange == "NASDAQ"
    assert good.category == "US_STOCK"
    assert good.tradable_status == "OC"
    assert good.tradable is True
    assert good.source == "WEBULL_SCREENER"
    assert universe.row_for("GOOD")["instrument_id"] == "913256135"


def test_valid_candidate_reaches_probe_then_30_day_bars_warmup():
    engine, _, market_data = build(behavior={"BAD": "unsupported"})

    assert engine.refresh_universe() == ("GOOD",)
    assert [call[3] for call in market_data.calls if call[0] == "GOOD"] == [
        "1",
        "30",
    ]
    assert engine.snapshot().warmup_result.successful_records[0].symbol == "GOOD"


def test_unsupported_is_rejected_once_and_does_not_abort_other_candidates():
    events.clear()
    engine, _, market_data = build(behavior={"BAD": "unsupported"})

    assert engine.refresh_universe() == ("GOOD",)
    result = engine.warmup_result
    assert len(result.unsupported_rejections) == 1
    rejection = result.unsupported_rejections[0]
    assert rejection.symbol == "BAD"
    assert rejection.retryable is False
    assert rejection.environment == "SANDBOX"
    assert [call for call in market_data.calls if call[0] == "BAD"] == [
        ("BAD", "US_STOCK", "D1", "1")
    ]
    assert events == [
        {
            "event_type": "symbol_rejected",
            "symbol": "BAD",
            "api_symbol": "BAD",
            "reason": "unsupported_symbol",
            "stage": "reference_warmup",
            "environment": "SANDBOX",
            "endpoint": "stock_bars",
        }
    ]


def test_environment_support_cache_is_isolated_and_force_refresh_is_available():
    cache = EnvironmentSupportCache(clock=lambda: NOW)
    sandbox, _, sandbox_data = build(
        "SANDBOX",
        {"BAD": "unsupported", "GOOD": "unsupported"},
        cache,
    )
    production, _, production_data = build("PRODUCTION", {}, cache)

    assert sandbox.refresh_universe() == ()
    assert production.refresh_universe() == ("BAD", "GOOD")
    sandbox.refresh_universe()
    assert len(sandbox_data.calls) == 2
    sandbox.refresh_universe(force_reference_refresh=True)
    assert len(sandbox_data.calls) == 4
    assert production_data.calls


def test_temporary_failures_remain_retryable():
    engine, _, market_data = build(
        behavior={"BAD": [TimeoutError("temporary"), None, None]}
    )

    assert engine.refresh_universe() == ("GOOD",)
    assert engine.warmup_result.temporary_failures[0].retryable is True
    assert engine.refresh_universe() == ("BAD", "GOOD")
    assert len([call for call in market_data.calls if call[0] == "BAD"]) == 3


def test_only_warmed_symbols_are_subscribed_and_empty_universe_fails_closed():
    engine, _, _ = build(behavior={"BAD": "unsupported"})
    transport = Transport()
    coordinator = LiveScannerCoordinator(transport, engine)

    assert coordinator.start() == ("GOOD",)
    assert transport.calls[-1] == ("subscribe", ("GOOD",))

    empty_engine, _, _ = build(
        behavior={"BAD": "unsupported", "GOOD": "unsupported"}
    )
    empty_transport = Transport()
    empty = LiveScannerCoordinator(empty_transport, empty_engine)
    assert empty.start() == ()
    assert not any(
        isinstance(call, tuple) and call[0] == "subscribe"
        for call in empty_transport.calls
    )
    snapshot = empty.snapshot()
    assert snapshot.active_symbols == ()
    assert snapshot.healthy is False
    assert "selected market-data environment" in snapshot.health_reason


def test_sdk_logger_configuration_is_single_and_non_propagating():
    logger = configure_official_sdk_logging()
    configure_official_sdk_logging()

    assert logger.name == "webull.core"
    assert logger.propagate is False
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.NullHandler)


def test_atlas_logs_redact_keys_signatures_secrets_and_signed_headers():
    class Sink:
        def __init__(self):
            self.records = []

        def emit(self, record):
            self.records.append(record)

    sink = Sink()
    StructuredLogger(sink).log(
        "stock_bars",
        "failed",
        api_key="visible-key",
        app_key="visible-app-key",
        app_secret="visible-secret",
        x_signature="visible-signature",
        signed_headers={"x-app-key": "nested-key"},
        authorization="Bearer visible-token",
        symbol="BAD",
    )
    text = repr(sink.records)

    assert "visible-" not in text
    assert "Bearer" not in text
    assert sink.records[0]["symbol"] == "BAD"


class StreamClient:
    def __init__(self):
        self._transport = "websockets"
        self.on_quotes_message = None
        self.on_connect_success = None
        self.on_disconnect = None

    def connect_and_loop_start(self, **kwargs):
        self.on_connect_success(self, object(), "session")

    def subscribe(self, **kwargs):
        return None

    def loop_stop(self):
        return None

    def disconnect(self):
        self.on_disconnect(self, None, 0)


def test_deliberate_shutdown_is_not_connection_failure():
    lifecycle = []
    client = StreamClient()
    backend = OfficialSdkStreamBackend(
        client,
        subscription_mapper=lambda symbols: {
            "symbols": symbols,
            "category": "US_STOCK",
            "sub_types": ("QUOTE",),
        },
    )
    backend.set_lifecycle_sink(
        lambda event, attempt, error: lifecycle.append(event)
    )

    backend.connect()
    backend.subscribe(("GOOD",))
    client.on_quotes_message(client, "quote", object())
    assert backend.receive() is not None
    backend.disconnect()

    assert lifecycle[:4] == [
        "websocket_http_upgrade",
        "mqtt_connack",
        "rest_subscription_requested",
        "rest_subscription_active",
    ]
    assert lifecycle[-1] == "deliberate_shutdown"
    assert "active_event_consumption" in lifecycle
    assert "unexpected_stream_termination" not in lifecycle
