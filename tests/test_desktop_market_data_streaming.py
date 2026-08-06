from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event

import pytest

from app.broker_plugins import BrokerCapabilities, BrokerRuntime
from app.broker_protocol.models import BrokerAccount, BrokerCash
from app.composition.runtime_projection_pipeline import (
    create_runtime_projection_pipeline,
)
from app.configuration import OperationalConfiguration, TradingEnvironment
from app.live_execution.account_polling import BrokerAccountSnapshot
from app.live_execution.broker_factory import (
    build_webull_market_data_stream,
)
from app.live_scanner.transport import ReceiveTransportAdapter
from app.market_data.models import (
    MarketEvent,
    MarketEventType,
    QuotePayload,
    TradePayload,
)
from app.operations.runtime import (
    PaperRuntimeEvent,
    RuntimeHealthUpdate,
)
from app.operations_core import ApplicationStateStore, OperationsBus
from app.services.market_event_translation import translate_market_event
from app.services.runtime_drivers import DesktopBrokerRuntimeDriver
from app.webull.configuration import ReconnectPolicy
from app.webull.errors import SerializationError
from app.webull.market_event_parser import WebullMarketEventParser
from app.webull.websocket_client import WebullWebSocketClient


NOW = datetime(2026, 7, 30, 15, 0, tzinfo=UTC)


def configuration(
    *,
    streaming: bool = True,
) -> OperationalConfiguration:
    return OperationalConfiguration(
        environment=TradingEnvironment.PAPER,
        broker_provider="webull",
        account_id="paper-account",
        api_key="app-key",
        api_secret="app-secret",
        api_base_url="https://api.sandbox.webull.com",
        stream_url="wss://data-api.sandbox.webull.com:8883/mqtt",
        authorization_database_path=Path("authorization.sqlite3"),
        execution_database_path=Path("execution.sqlite3"),
        market_event_database_path=Path("market.sqlite3"),
        emergency_stop_database_path=Path("stop.sqlite3"),
        log_level="INFO",
        health_port=8080,
        live_trading_enabled=False,
        max_order_notional=Decimal("10"),
        max_daily_notional=Decimal("50"),
        max_open_positions=1,
        max_open_orders=1,
        max_order_rate=5,
        max_quantity_per_symbol=Decimal("1"),
        allowed_symbols=("AAPL",),
        blocked_symbols=(),
        maximum_market_data_age_seconds=5,
        reconciliation_interval_seconds=30,
        maximum_reconciliation_age_seconds=60,
        maximum_unresolved_mutations=0,
        market_data_streaming_enabled=streaming,
        market_data_symbols=("AAPL",),
        stream_reconnect_attempts=2,
        stream_reconnect_backoff_seconds=Decimal("0.01"),
    )


class FakeBroker:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def connect(self) -> None:
        self.calls.append("connect")

    def disconnect(self) -> None:
        self.calls.append("disconnect")

    def submit_order(self, order):
        raise AssertionError("market data must not submit orders")

    def cancel_order(self, client_order_id):
        raise AssertionError("market data must not cancel orders")

    def replace_order(self, client_order_id, order):
        raise AssertionError("market data must not replace orders")


class FakeStream:
    def __init__(
        self,
        events: list[MarketEvent | None],
        *,
        lifecycle: tuple[str, int, Exception | None] | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.events = list(events)
        self.lifecycle = lifecycle
        self.failure = failure
        self.calls: list[object] = []
        self.lifecycle_sink = None

    def set_lifecycle_sink(self, sink) -> None:
        self.lifecycle_sink = sink

    def connect(self) -> None:
        self.calls.append("connect")

    def subscribe(self, symbols: tuple[str, ...]) -> None:
        self.calls.append(("subscribe", symbols))

    def read_event(self) -> MarketEvent | None:
        if self.lifecycle is not None:
            lifecycle = self.lifecycle
            self.lifecycle = None
            self.lifecycle_sink(*lifecycle)
        if self.failure is not None:
            failure = self.failure
            self.failure = None
            raise failure
        if self.events:
            return self.events.pop(0)
        return None

    def disconnect(self) -> None:
        self.calls.append("disconnect")


def quote_event() -> MarketEvent:
    return MarketEvent(
        sequence=1,
        timestamp=NOW,
        symbol="AAPL",
        source="webull",
        event_type=MarketEventType.QUOTE,
        payload=QuotePayload(
            bid=Decimal("199"),
            ask=Decimal("201"),
            bid_size=Decimal("10"),
            ask_size=Decimal("12"),
        ),
    )


def trade_event() -> MarketEvent:
    return MarketEvent(
        sequence=2,
        timestamp=NOW + timedelta(seconds=1),
        symbol="AAPL",
        source="webull",
        event_type=MarketEventType.TRADE,
        payload=TradePayload(
            price=Decimal("200.50"),
            size=Decimal("25"),
            trade_id="trade-1",
        ),
    )


def runtime(broker: FakeBroker, stream: object | None) -> BrokerRuntime:
    return BrokerRuntime(
        provider="webull",
        capabilities=BrokerCapabilities(
            provider="webull",
            version="test",
            supports_execution=True,
            supports_account_data=True,
            supports_market_data=True,
            supports_streaming=True,
        ),
        execution=broker,
        market_data=stream,
    )


def account_snapshot() -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        account=BrokerAccount(
            account_id_redacted="******ount",
            account_type="PAPER",
            status="ACTIVE",
        ),
        cash=BrokerCash(
            settled_cash=Decimal("1000"),
            unsettled_cash=Decimal("0"),
            currency="USD",
        ),
        positions=(),
        orders=(),
        observed_at=NOW,
    )


def test_stream_is_constructed_through_existing_dependency_boundaries() -> None:
    captured: dict[str, object] = {}
    backend = object()
    client = object()

    def backend_factory(credentials, subscription, **kwargs):
        captured["credentials"] = credentials
        captured["subscription"] = subscription
        captured["backend_kwargs"] = kwargs
        return backend

    def client_factory(*args):
        captured["client_args"] = args
        return client

    subscription = object()
    stream = build_webull_market_data_stream(
        configuration(),
        subscription_factory=lambda: subscription,
        backend_factory=backend_factory,
        client_factory=client_factory,
        session_id_factory=lambda: "atlas-test",
    )

    assert isinstance(stream, ReceiveTransportAdapter)
    assert stream.client is client
    assert captured["credentials"].session_id == "atlas-test"
    assert captured["subscription"] is subscription
    assert captured["backend_kwargs"] == {
        "receive_timeout_seconds": 1.0,
        "http_host": "api.sandbox.webull.com",
        "mqtt_host": "data-api.sandbox.webull.com",
        "mqtt_port": 1883,
        "tls_enable": True,
        "transport": "tcp",
        "websocket_path": None,
    }
    assert captured["client_args"][0] is backend
    assert isinstance(captured["client_args"][1], WebullMarketEventParser)


def test_disabled_streaming_constructs_no_sdk_boundary() -> None:
    stream = build_webull_market_data_stream(
        configuration(streaming=False),
        subscription_factory=lambda: pytest.fail(
            "disabled streaming must not load the SDK"
        ),
        backend_factory=lambda *args, **kwargs: pytest.fail(
            "disabled streaming must not construct a backend"
        ),
    )

    assert stream is None


def test_quote_and_trade_translate_to_existing_runtime_events() -> None:
    quote = translate_market_event(
        quote_event(),
        sequence=10,
        source="desktop",
        cycle=3,
    )
    trade = translate_market_event(
        trade_event(),
        sequence=11,
        source="desktop",
        cycle=3,
    )

    assert quote.event_type == "QUOTE_UPDATED"
    assert quote.mark_price == Decimal("200")
    assert quote.watchlist.quote.bid == Decimal("199")
    assert quote.watchlist.quote.ask == Decimal("201")
    assert trade.event_type == "MARK_UPDATED"
    assert trade.mark_price == Decimal("200.50")
    assert trade.watchlist.quote.latest_price == Decimal("200.50")
    assert trade.watchlist.quote.volume == 25


def test_driver_streams_quotes_through_application_state_and_stops() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    projections = create_runtime_projection_pipeline(
        operations_bus=bus,
        account_id="paper-account",
        watchlist_stale_after=timedelta(seconds=5),
    )
    broker = FakeBroker()
    stream = FakeStream([quote_event(), trade_event()])
    stop_event = Event()
    event_types: list[str] = []
    observed_market_events: list[MarketEvent] = []

    def sink(event: PaperRuntimeEvent) -> None:
        event_types.append(event.event_type)
        projections.sink(event)
        if event.event_type == "MARK_UPDATED":
            stop_event.set()

    driver = DesktopBrokerRuntimeDriver(
        configuration=configuration(),
        broker_runtime=runtime(broker, stream),
        event_sink=sink,
        account_snapshot_sink=lambda snapshot: None,
        account_poller=lambda broker, *, clock: account_snapshot(),
        market_event_observer=observed_market_events.append,
        clock=lambda: NOW + timedelta(seconds=2),
    )

    try:
        driver.run(stop_event=stop_event, cycle_sink=lambda cycle: None)

        entry = store.snapshot().watchlist_projection.entries[0]
        assert entry.symbol == "AAPL"
        assert entry.latest_price == "200.50"
        assert entry.bid == "199"
        assert entry.ask == "201"
        assert entry.volume == 25
        assert entry.last_update == NOW + timedelta(seconds=1)
        assert entry.stale is False
        assert stream.calls == [
            "connect",
            ("subscribe", ("AAPL",)),
            "disconnect",
        ]
        assert broker.calls == ["connect", "disconnect"]
        assert observed_market_events == [quote_event(), trade_event()]
        assert "MARKET_DATA_CONNECTING" in event_types
        assert "MARKET_DATA_CONNECTED" in event_types
        assert "MARKET_DATA_SUBSCRIBED" in event_types
        assert "MARKET_DATA_DISCONNECTED" in event_types
    finally:
        store.close()


def test_receive_heartbeat_advances_watchlist_freshness() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    projections = create_runtime_projection_pipeline(
        operations_bus=bus,
        account_id="paper-account",
        watchlist_stale_after=timedelta(seconds=5),
    )
    quote = translate_market_event(
        quote_event(),
        sequence=2,
        source="desktop",
        cycle=0,
    )

    try:
        projections.sink(
            PaperRuntimeEvent(
                sequence=1,
                timestamp=NOW,
                event_type="SYMBOL_SUBSCRIBED",
                message="Subscribed.",
                cycle=0,
                symbol="AAPL",
                source="desktop",
            )
        )
        projections.sink(quote)
        projections.sink(
            PaperRuntimeEvent(
                sequence=3,
                timestamp=NOW + timedelta(seconds=6),
                event_type="MARKET_DATA_HEARTBEAT",
                message="Receive loop is healthy.",
                cycle=0,
                source="desktop",
                health=RuntimeHealthUpdate(
                    market_data_status="CONNECTED",
                ),
            )
        )

        assert store.snapshot().watchlist_projection.entries[0].stale is True
    finally:
        store.close()


def test_reconnect_lifecycle_is_published_to_existing_health_path() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    projections = create_runtime_projection_pipeline(
        operations_bus=bus,
        account_id="paper-account",
    )
    broker = FakeBroker()
    stream = FakeStream(
        [trade_event()],
        lifecycle=("reconnecting", 1, OSError("connection lost")),
    )
    stop_event = Event()
    event_types: list[str] = []

    def sink(event: PaperRuntimeEvent) -> None:
        event_types.append(event.event_type)
        projections.sink(event)
        if event.event_type == "MARK_UPDATED":
            stop_event.set()

    driver = DesktopBrokerRuntimeDriver(
        configuration=configuration(),
        broker_runtime=runtime(broker, stream),
        event_sink=sink,
        account_snapshot_sink=lambda snapshot: None,
        account_poller=lambda broker, *, clock: account_snapshot(),
        clock=lambda: NOW,
    )

    try:
        driver.run(stop_event=stop_event, cycle_sink=lambda cycle: None)

        assert "MARKET_DATA_RECONNECTING" in event_types
        assert store.snapshot().health_projection.reconnect_attempts == 1
    finally:
        store.close()


def test_malformed_payload_publishes_parse_and_terminal_health() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    projections = create_runtime_projection_pipeline(
        operations_bus=bus,
        account_id="paper-account",
    )
    broker = FakeBroker()
    stream = FakeStream(
        [],
        lifecycle=(
            "parse_failed",
            0,
            SerializationError("malformed payload"),
        ),
        failure=SerializationError("malformed payload"),
    )
    events: list[PaperRuntimeEvent] = []
    stop_event = Event()

    def sink(event: PaperRuntimeEvent) -> None:
        events.append(event)
        projections.sink(event)
        if event.event_type == "MARKET_DATA_TERMINAL_FAILURE":
            stop_event.set()

    driver = DesktopBrokerRuntimeDriver(
        configuration=configuration(),
        broker_runtime=runtime(broker, stream),
        event_sink=sink,
        account_snapshot_sink=lambda snapshot: None,
        account_poller=lambda broker, *, clock: account_snapshot(),
        clock=lambda: NOW,
    )

    try:
        driver.run(stop_event=stop_event, cycle_sink=lambda cycle: None)

        event_types = [event.event_type for event in events]
        assert "MARKET_DATA_PARSE_FAILED" in event_types
        assert "MARKET_DATA_TERMINAL_FAILURE" in event_types
        degraded = next(
            event
            for event in events
            if event.event_type == "MARKET_DATA_PARSE_FAILED"
        )
        assert degraded.health.market_data_status == "STREAM_PARTIALLY_DEGRADED"
        assert degraded.health.streaming_status == "STREAM_PARTIALLY_DEGRADED"
        assert degraded.health.market_data_rest_status == "AVAILABLE"
        terminal = next(
            event
            for event in events
            if event.event_type == "MARKET_DATA_TERMINAL_FAILURE"
        )
        assert terminal.health.runtime_status == "DEGRADED"
        assert terminal.health.market_data_status == "REST_ONLY"
        assert terminal.health.market_data_rest_status == "AVAILABLE"
        assert terminal.health.streaming_status == "UNAVAILABLE"
        assert not any(
            call in broker.calls
            for call in ("submit_order", "cancel_order", "replace_order")
        )
    finally:
        store.close()


def test_protocol_failure_is_nonfatal_and_classified_as_rest_only() -> None:
    class ProtocolFailureStream(FakeStream):
        def connect(self) -> None:
            self.calls.append("connect")
            raise RuntimeError("loop ack code: 1, msg: Protocol not supported")

    stop_event = Event()
    events: list[PaperRuntimeEvent] = []
    broker = FakeBroker()
    stream = ProtocolFailureStream([])

    def poller(broker_value, *, clock):
        stop_event.set()
        return account_snapshot()

    driver = DesktopBrokerRuntimeDriver(
        configuration=configuration(),
        broker_runtime=runtime(broker, stream),
        event_sink=events.append,
        account_snapshot_sink=lambda snapshot: None,
        account_poller=poller,
        clock=lambda: NOW,
    )

    driver.run(stop_event=stop_event, cycle_sink=lambda cycle: None)

    terminal = next(
        event
        for event in events
        if event.event_type == "MARKET_DATA_TERMINAL_FAILURE"
    )
    assert terminal.health.runtime_status == "DEGRADED"
    assert terminal.health.market_data_status == "REST_ONLY"
    assert terminal.health.market_data_rest_status == "AVAILABLE"
    assert terminal.health.streaming_status == "PROTOCOL_UNSUPPORTED"
    assert broker.calls == ["connect", "disconnect"]
    assert stream.calls == ["connect", "disconnect"]


def test_session_registration_failure_keeps_rest_available() -> None:
    class RegistrationFailureStream(FakeStream):
        def subscribe(self, symbols: tuple[str, ...]) -> None:
            self.calls.append(("subscribe", symbols))
            raise RuntimeError("INVALID_SESSION")

    stop_event = Event()
    events: list[PaperRuntimeEvent] = []
    broker = FakeBroker()
    stream = RegistrationFailureStream([])

    def poller(broker_value, *, clock):
        stop_event.set()
        return account_snapshot()

    driver = DesktopBrokerRuntimeDriver(
        configuration=configuration(),
        broker_runtime=runtime(broker, stream),
        event_sink=events.append,
        account_snapshot_sink=lambda snapshot: None,
        account_poller=poller,
        clock=lambda: NOW,
    )

    driver.run(stop_event=stop_event, cycle_sink=lambda cycle: None)

    terminal = next(
        event for event in events
        if event.event_type == "MARKET_DATA_TERMINAL_FAILURE"
    )
    assert terminal.health.market_data_status == "REST_ONLY"
    assert terminal.health.market_data_rest_status == "AVAILABLE"
    assert not any(
        call in broker.calls
        for call in ("submit_order", "cancel_order", "replace_order")
    )


def test_websocket_client_reconnects_and_resubscribes() -> None:
    class Backend:
        def __init__(self) -> None:
            self.receive_calls = 0
            self.connect_calls = 0
            self.subscriptions: list[tuple[str, ...]] = []

        def connect(self) -> None:
            self.connect_calls += 1

        def disconnect(self) -> None:
            pass

        def subscribe(self, symbols: tuple[str, ...]) -> None:
            self.subscriptions.append(symbols)

        def receive(self):
            self.receive_calls += 1
            if self.receive_calls == 1:
                raise OSError("connection lost")
            return {
                "event_type": "TRADE",
                "symbol": "AAPL",
                "last_price": "200.50",
                "size": "1",
                "timestamp": NOW.isoformat(),
            }

    class Logger:
        def log(self, *args, **kwargs) -> None:
            pass

    backend = Backend()
    lifecycle = []
    client = WebullWebSocketClient(
        backend,
        WebullMarketEventParser(clock=lambda: NOW),
        ReconnectPolicy(
            maximum_attempts=1,
            backoff_seconds=Decimal("0.01"),
        ),
        lambda seconds: None,
        Logger(),
        lifecycle_sink=lambda *values: lifecycle.append(values),
    )
    client.connect()
    client.subscribe(("AAPL",))

    event = client.receive()

    assert event.event_type is MarketEventType.TRADE
    assert backend.connect_calls == 2
    assert backend.subscriptions == [("AAPL",), ("AAPL",)]
    assert [item[0] for item in lifecycle] == [
        "reconnecting",
        "reconnected",
    ]
