from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Event

import pytest

from app.broker_plugins import BrokerCapabilities, BrokerRuntime
from app.broker_protocol.models import (
    BrokerAccount,
    BrokerCash,
)
from app.composition.desktop_broker_runtime import (
    create_configured_desktop_broker_driver,
)
from app.configuration import OperationalConfiguration, TradingEnvironment
from app.operations.runtime import PaperRuntimeEvent
from app.operations_core import (
    ApplicationStateStore,
    HealthUpdated,
    OperationsBus,
)
from app.read_models.health_projection import HealthProjection
from app.services.runtime_drivers import DesktopBrokerRuntimeDriver


NOW = datetime(2026, 7, 30, 15, 0, tzinfo=UTC)


def configuration() -> OperationalConfiguration:
    return OperationalConfiguration(
        environment=TradingEnvironment.PAPER,
        broker_provider="webull",
        account_id="paper-account",
        api_key="key",
        api_secret="secret",
        api_base_url="https://sandbox.example",
        stream_url="wss://sandbox.example/stream",
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
    )


class FakeBroker:
    def __init__(self, *, connection_error: Exception | None = None) -> None:
        self.connection_error = connection_error
        self.calls: list[str] = []

    def connect(self) -> None:
        self.calls.append("connect")
        if self.connection_error is not None:
            raise self.connection_error

    def disconnect(self) -> None:
        self.calls.append("disconnect")

    def submit_order(self, order):
        self.calls.append("submit_order")
        raise AssertionError("orders are outside Milestone 1")

    def cancel_order(self, client_order_id):
        self.calls.append("cancel_order")
        raise AssertionError("orders are outside Milestone 1")

    def replace_order(self, client_order_id, order):
        self.calls.append("replace_order")
        raise AssertionError("orders are outside Milestone 1")

    def get_positions(self):
        self.calls.append("get_positions")
        return ()

    def get_orders(self):
        self.calls.append("get_orders")
        return ()

    def get_cash(self):
        self.calls.append("get_cash")
        return BrokerCash(
            settled_cash=Decimal("1000"),
            unsettled_cash=Decimal("0"),
            currency="USD",
            buying_power=Decimal("900"),
            equity=Decimal("1000"),
        )

    def get_account(self):
        self.calls.append("get_account")
        return BrokerAccount(
            account_id_redacted="******ount",
            account_type="CASH",
            status="ACTIVE",
        )

    def get_fills(self):
        self.calls.append("get_fills")
        raise AssertionError("account polling is outside Milestone 1")


def broker_runtime(broker: FakeBroker) -> BrokerRuntime:
    return BrokerRuntime(
        provider="webull",
        capabilities=BrokerCapabilities(
            provider="webull",
            version="test",
            supports_execution=True,
            supports_account_data=True,
        ),
        execution=broker,
    )


def test_configured_driver_resolves_selected_broker_plugin() -> None:
    configured = configuration()
    broker = FakeBroker()
    captured: dict[str, object] = {}

    def runtime_factory(**kwargs) -> BrokerRuntime:
        captured.update(kwargs)
        return broker_runtime(broker)

    webull_factory = lambda value: broker
    events: list[PaperRuntimeEvent] = []

    driver = create_configured_desktop_broker_driver(
        event_sink=events.append,
        account_snapshot_sink=lambda snapshot: None,
        configuration_loader=lambda: configured,
        broker_runtime_factory=runtime_factory,
        webull_broker_factory=webull_factory,
        clock=lambda: NOW,
        source="test-broker-session",
    )

    assert isinstance(driver, DesktopBrokerRuntimeDriver)
    assert driver.environment == "PAPER"
    assert driver.active_model == "webull broker"
    assert captured == {
        "provider": "webull",
        "configuration": configured,
        "webull_broker_factory": webull_factory,
    }


def test_driver_authenticates_owns_lifecycle_and_does_no_other_broker_work() -> None:
    broker = FakeBroker()
    events: list[PaperRuntimeEvent] = []
    stop_event = Event()
    stop_event.set()
    driver = DesktopBrokerRuntimeDriver(
        configuration=configuration(),
        broker_runtime=broker_runtime(broker),
        event_sink=events.append,
        account_snapshot_sink=lambda snapshot: None,
        clock=lambda: NOW,
        source="test-broker-session",
    )

    driver.run(stop_event=stop_event, cycle_sink=lambda cycle: None)

    assert broker.calls == ["connect", "disconnect"]
    assert [event.event_type for event in events] == [
        "BROKER_CONNECTING",
        "BROKER_AUTHENTICATED",
        "BROKER_DISCONNECTED",
    ]
    assert [event.health.broker_status for event in events] == [
        "CONNECTING",
        "CONNECTED",
        "DISCONNECTED",
    ]


def test_authentication_failure_is_published_and_fails_closed() -> None:
    broker = FakeBroker(connection_error=RuntimeError("invalid credentials"))
    events: list[PaperRuntimeEvent] = []
    driver = DesktopBrokerRuntimeDriver(
        configuration=configuration(),
        broker_runtime=broker_runtime(broker),
        event_sink=events.append,
        account_snapshot_sink=lambda snapshot: None,
        clock=lambda: NOW,
    )

    with pytest.raises(RuntimeError, match="invalid credentials"):
        driver.run(stop_event=Event(), cycle_sink=lambda cycle: None)

    assert broker.calls == ["connect"]
    assert [event.event_type for event in events] == [
        "BROKER_CONNECTING",
        "BROKER_AUTHENTICATION_FAILED",
    ]
    assert events[-1].health.runtime_status == "FAILED"
    assert events[-1].health.broker_status == "DISCONNECTED"
    assert events[-1].health.last_error == (
        "RuntimeError: invalid credentials"
    )


def test_broker_health_events_flow_through_existing_health_projection() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    updates: list[HealthUpdated] = []
    bus.subscribe(HealthUpdated, updates.append)
    projection = HealthProjection(bus)
    broker = FakeBroker()
    stop_event = Event()
    stop_event.set()
    driver = DesktopBrokerRuntimeDriver(
        configuration=configuration(),
        broker_runtime=broker_runtime(broker),
        event_sink=projection,
        account_snapshot_sink=lambda snapshot: None,
        clock=lambda: NOW,
    )

    try:
        driver.run(stop_event=stop_event, cycle_sink=lambda cycle: None)

        assert [
            update.state.broker_status
            for update in updates
        ] == ["CONNECTING", "CONNECTED", "DISCONNECTED"]
        assert store.snapshot().health_projection.broker_status == (
            "DISCONNECTED"
        )
        assert store.snapshot().health_projection.runtime_status == "STOPPED"
    finally:
        store.close()


def test_driver_polls_account_until_stopped_and_reports_cycles() -> None:
    broker = FakeBroker()
    snapshots = []
    cycles = []
    stop_event = Event()
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    health_projection = HealthProjection(bus)
    broker_health_during_poll = []

    def receive_snapshot(snapshot) -> None:
        snapshots.append(snapshot)
        broker_health_during_poll.append(
            store.snapshot().health_projection.broker_status
        )
        stop_event.set()

    driver = DesktopBrokerRuntimeDriver(
        configuration=configuration(),
        broker_runtime=broker_runtime(broker),
        event_sink=health_projection,
        account_snapshot_sink=receive_snapshot,
        clock=lambda: NOW,
    )

    try:
        driver.run(stop_event=stop_event, cycle_sink=cycles.append)

        assert broker.calls == [
            "connect",
            "get_account",
            "get_cash",
            "get_positions",
            "get_orders",
            "disconnect",
        ]
        assert len(snapshots) == 1
        assert snapshots[0].observed_at == NOW
        assert broker_health_during_poll == ["CONNECTED"]
        assert cycles == [1]
        assert driver.cycles_completed == 1
    finally:
        store.close()
