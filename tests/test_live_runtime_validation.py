from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Event

import pytest

from app.broker_plugins import BrokerCapabilities, BrokerRuntime
from app.broker_protocol.models import BrokerAccount, BrokerCash
from app.configuration import (
    MarketDataConfiguration,
    OperationalConfiguration,
    TradingConfiguration,
    TradingEnvironment,
)
from app.live_execution.account_polling import BrokerAccountSnapshot
from app.realtime_scanner import ScannerSnapshot
from app.services.runtime_drivers import DesktopBrokerRuntimeDriver
from app.webull.market_data_probe import (
    CapabilityStatus,
    MarketDataProbeResult,
    ProbeState,
    SymbolCapabilityResult,
    SymbolProbeState,
)
from app.webull.market_data_session import MarketDataSession
from app.webull.startup_validation import (
    StartupValidationResult,
    TradingProbeResult,
    TradingProbeState,
)


NOW = datetime(2026, 7, 31, 15, 0, tzinfo=UTC)
AVAILABLE = CapabilityStatus(ProbeState.AVAILABLE)
NOT_TESTED = CapabilityStatus(ProbeState.NOT_TESTED)


class Broker:
    def __init__(self, order):
        self.order = order

    def connect(self):
        self.order.append("trading.connect")

    def disconnect(self):
        self.order.append("trading.disconnect")


class Boundary:
    def set_lifecycle_sink(self, sink):
        self.sink = sink


class Scanner:
    def __init__(self, order):
        self.order = order

    def set_event_observer(self, observer):
        self.observer = observer

    def start(self, **kwargs):
        self.order.append("scanner.start")
        return ("AAPL", "SPY", "TSLA", "MSFT", "NVDA")

    def snapshot(self):
        return ScannerSnapshot(
            NOW,
            ("AAPL", "SPY", "TSLA", "MSFT", "NVDA"),
            (),
            (),
            0,
            0,
            (),
        )

    def stop(self):
        self.order.append("scanner.stop")

    def disconnect(self):
        self.order.append("scanner.disconnect")


class Validator:
    def __init__(self, order, result):
        self.order = order
        self.result = result
        self.calls = 0

    def run(self):
        self.calls += 1
        self.order.append("startup.validate")
        return self.result


def configuration(
    market_environment=TradingEnvironment.PRODUCTION,
) -> OperationalConfiguration:
    trading = TradingConfiguration(
        TradingEnvironment.TEST, "paper-account", "trade-key", "trade-secret",
        "https://api.sandbox.webull.com", "wss://data-api.sandbox.webull.com/mqtt",
    )
    market = MarketDataConfiguration(
        market_environment, "data-key", "data-secret",
        "https://api.webull.com", "wss://data-api.webull.com/mqtt",
    )
    return OperationalConfiguration(
        TradingEnvironment.TEST, "webull", "paper-account", "legacy-key",
        "legacy-secret", trading.api_base_url, trading.stream_url,
        Path("authorization.sqlite3"), Path("execution.sqlite3"),
        Path("market.sqlite3"), Path("stop.sqlite3"), "INFO", 8080, False,
        Decimal("10"), Decimal("50"), 1, 1, 5, Decimal("1"), (), (),
        5, 1, 60, 0, True, (), 3, Decimal("1"), trading, market,
    )


def trading_result(ready=True):
    return TradingProbeResult(
        "TEST", "fp_trade", TradingProbeState.CONNECTED,
        TradingProbeState.CONNECTED,
        TradingProbeState.OK if ready else TradingProbeState.FAILED,
        TradingProbeState.OK,
        TradingProbeState.ENABLED,
    )


def market_result(
    *,
    environment="PRODUCTION",
    bars=AVAILABLE,
    entitlement=AVAILABLE,
    subscription=AVAILABLE,
    reconnect=AVAILABLE,
    credentials=AVAILABLE,
    current_session=MarketDataSession.CLOSED,
):
    symbol_state = (
        SymbolProbeState.NO_ENTITLEMENT
        if entitlement.state is ProbeState.NOT_ENTITLED
        else SymbolProbeState.UNSUPPORTED
        if bars.state is ProbeState.UNSUPPORTED
        else SymbolProbeState.UNKNOWN
        if not subscription.available
        else SymbolProbeState.SUPPORTED
    )
    symbols = tuple(
        SymbolCapabilityResult(
            symbol, bars, AVAILABLE, AVAILABLE, AVAILABLE, subscription,
            symbol_state,
        )
        for symbol in ("AAPL", "SPY", "TSLA", "MSFT", "NVDA")
    )
    return MarketDataProbeResult(
        environment, "fp_data", AVAILABLE, credentials, bars, AVAILABLE,
        AVAILABLE, AVAILABLE, subscription, AVAILABLE, reconnect,
        entitlement, AVAILABLE, symbols,
        current_session=current_session,
    )


def runtime(order, validation):
    broker = Broker(order)
    return broker, BrokerRuntime(
        "webull",
        BrokerCapabilities(
            "webull", "test", supports_execution=True,
            supports_account_data=True, supports_market_data=True,
            supports_streaming=True,
        ),
        broker,
        Boundary(),
    ), Validator(order, validation)


def snapshot():
    return BrokerAccountSnapshot(
        BrokerAccount("******ount", "PAPER", "ACTIVE"),
        BrokerCash(
            Decimal("1000"), Decimal("0"), "USD", buying_power=Decimal("900")
        ),
        (), (), NOW,
    )


def test_sandbox_trading_and_production_market_data_validate_before_scanner():
    order = []
    result = StartupValidationResult(trading_result(), market_result())
    broker, broker_runtime, validator = runtime(order, result)
    scanner = Scanner(order)
    events = []
    stop = Event()
    stop.set()
    driver = DesktopBrokerRuntimeDriver(
        configuration=configuration(), broker_runtime=broker_runtime,
        event_sink=events.append, account_snapshot_sink=lambda value: None,
        scanner_coordinator=scanner, startup_validator=validator,
        clock=lambda: NOW,
    )

    driver.run(stop_event=stop, cycle_sink=lambda cycle: None)

    assert order.index("trading.connect") < order.index("startup.validate")
    assert order.index("startup.validate") < order.index("scanner.start")
    assert validator.calls == 1
    probe_event = next(e for e in events if e.event_type == "MARKET_DATA_PROBE_COMPLETED")
    assert probe_event.health.market_data_environment == "PRODUCTION"
    assert probe_event.health.market_data_status == "READY"
    assert probe_event.health.subscription_status == "ACCEPTED"
    assert probe_event.health.probe_aapl_status == "SUPPORTED"
    assert probe_event.health.probe_spy_status == "SUPPORTED"
    assert probe_event.health.probe_tsla_status == "SUPPORTED"
    assert probe_event.health.probe_msft_status == "SUPPORTED"
    assert probe_event.health.probe_nvda_status == "SUPPORTED"
    ready_event = next(e for e in events if e.event_type == "channels_subscribed")
    assert ready_event.health.scanner_status == "READY"
    assert ready_event.health.universe_status == "LOADED"
    assert ready_event.health.symbols_status == "VALIDATED"
    assert ready_event.health.reference_cache_status == "WARM"
    assert ready_event.health.ranking_status == "ACTIVE"
    assert ready_event.health.supported_symbols == 5


def test_sandbox_market_data_can_validate_without_crossing_trading_state():
    order = []
    result = StartupValidationResult(
        trading_result(), market_result(environment="TEST")
    )
    _, broker_runtime, validator = runtime(order, result)
    events = []
    stop = Event(); stop.set()
    DesktopBrokerRuntimeDriver(
        configuration=configuration(TradingEnvironment.TEST),
        broker_runtime=broker_runtime, event_sink=events.append,
        account_snapshot_sink=lambda value: None,
        scanner_coordinator=Scanner(order), startup_validator=validator,
        clock=lambda: NOW,
    ).run(stop_event=stop, cycle_sink=lambda cycle: None)
    market_event = next(e for e in events if e.event_type == "MARKET_DATA_PROBE_COMPLETED")
    trading_event = next(e for e in events if e.event_type == "TRADING_STARTUP_VALIDATED")
    assert trading_event.health.trading_environment == "TEST"
    assert market_event.health.market_data_environment == "TEST"


@pytest.mark.parametrize(
    ("market", "expected_status", "reason"),
    (
        (
            market_result(
                credentials=CapabilityStatus(ProbeState.CREDENTIALS_MISSING),
                subscription=NOT_TESTED, reconnect=NOT_TESTED,
            ),
            "DISABLED",
            "Production market-data credentials are missing.",
        ),
        (
            market_result(
                entitlement=CapabilityStatus(ProbeState.NOT_ENTITLED),
            ),
            "DISABLED",
            "Production market-data entitlement is not granted.",
        ),
        (
            market_result(subscription=CapabilityStatus(ProbeState.UNAVAILABLE)),
            "STREAM_CONNECTED_SUBSCRIPTION_DENIED",
            "STREAM_CONNECTED_SUBSCRIPTION_DENIED",
        ),
        (
            market_result(
                environment="TEST",
                bars=CapabilityStatus(ProbeState.UNSUPPORTED),
            ),
            "NO_SUPPORTED_SYMBOLS",
            "NO_SUPPORTED_SYMBOLS",
        ),
        (
            market_result(
                entitlement=CapabilityStatus(ProbeState.NOT_ENTITLED),
                subscription=CapabilityStatus(ProbeState.NOT_ENTITLED),
                current_session=MarketDataSession.OVERNIGHT,
            ),
            "DEGRADED",
            "OVERNIGHT_ENTITLEMENT_REQUIRED",
        ),
    ),
)
def test_market_failures_disable_scanner_but_keep_account_polling(
    market, expected_status, reason,
):
    order = []
    result = StartupValidationResult(trading_result(), market)
    _, broker_runtime, validator = runtime(order, result)
    scanner = Scanner(order)
    events = []
    stop = Event()

    def receive_account(value):
        order.append("account.poll")
        stop.set()

    driver = DesktopBrokerRuntimeDriver(
        configuration=configuration(), broker_runtime=broker_runtime,
        event_sink=events.append, account_snapshot_sink=receive_account,
        account_poller=lambda broker, *, clock: snapshot(),
        scanner_coordinator=scanner, startup_validator=validator,
        clock=lambda: NOW,
    )
    driver.run(stop_event=stop, cycle_sink=lambda cycle: None)

    assert "scanner.start" not in order
    assert "account.poll" in order
    assert validator.calls == 1
    probe = next(e for e in events if e.event_type == "MARKET_DATA_PROBE_COMPLETED")
    assert probe.health.market_data_status == expected_status
    assert any(reason in e.message for e in events)
    if reason == "OVERNIGHT_ENTITLEMENT_REQUIRED":
        assert probe.health.market_data_rest_status == "CONNECTED"
        assert probe.health.streaming_status == "CONNECTED"
        assert probe.health.entitlement_status == "NOT_SUBSCRIBED"
        assert probe.health.scanner_status == "PAUSED_UNTIL_PREMARKET"


def test_missing_reconnect_capability_prevents_scanner_start():
    order = []
    market = market_result(reconnect=CapabilityStatus(ProbeState.UNAVAILABLE))
    _, broker_runtime, validator = runtime(
        order, StartupValidationResult(trading_result(), market)
    )
    stop = Event(); stop.set()
    DesktopBrokerRuntimeDriver(
        configuration=configuration(), broker_runtime=broker_runtime,
        event_sink=lambda event: None, account_snapshot_sink=lambda value: None,
        scanner_coordinator=Scanner(order), startup_validator=validator,
        clock=lambda: NOW,
    ).run(stop_event=stop, cycle_sink=lambda cycle: None)
    assert "scanner.start" not in order
