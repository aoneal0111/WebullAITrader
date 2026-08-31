from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

import app.composition.desktop_broker_runtime as desktop_broker_module

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
from app.webull.sdk_market_data import WebullScannerUniverseProvider


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
    market_data_factory = lambda value: None
    events: list[PaperRuntimeEvent] = []

    driver = create_configured_desktop_broker_driver(
        event_sink=events.append,
        account_snapshot_sink=lambda snapshot: None,
        configuration_loader=lambda: configured,
        broker_runtime_factory=runtime_factory,
        webull_broker_factory=webull_factory,
        webull_market_data_factory=market_data_factory,
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
        "webull_market_data_factory": market_data_factory,
    }


def test_production_composition_owns_autonomous_webull_universe_provider(
    monkeypatch,
) -> None:
    configured = configuration()
    broker = FakeBroker()
    stream = object()
    captured = {}
    coordinator = object()

    def capture_scanner_infrastructure(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(coordinator=coordinator)

    monkeypatch.setattr(
        desktop_broker_module,
        "create_desktop_scanner_infrastructure",
        capture_scanner_infrastructure,
    )

    driver = create_configured_desktop_broker_driver(
        event_sink=lambda event: None,
        account_snapshot_sink=lambda snapshot: None,
        configuration_loader=lambda: configured,
        broker_runtime_factory=lambda **kwargs: BrokerRuntime(
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
        ),
        webull_broker_factory=lambda value: broker,
        webull_market_data_factory=lambda value: stream,
        clock=lambda: NOW,
    )

    provider = captured["universe_service"]._provider
    assert isinstance(provider, WebullScannerUniverseProvider)
    assert driver._scanner is coordinator
    assert configured.allowed_symbols == ("AAPL",)
    assert "default_channels" not in captured
    assert captured["maximum_events_per_cycle"] == 100


def test_experiment_sidecar_failures_do_not_escape_scanner_composition(
    monkeypatch,
) -> None:
    configured = configuration()
    broker = FakeBroker()
    stream = object()
    captured = {}

    coordinator = object()

    def capture_scanner_infrastructure(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(coordinator=coordinator)

    class FailingExperimentJournal:
        def __init__(self, path):
            raise RuntimeError("experiment journal unavailable")

    monkeypatch.setattr(
        desktop_broker_module,
        "create_desktop_scanner_infrastructure",
        capture_scanner_infrastructure,
    )
    monkeypatch.setattr(
        desktop_broker_module,
        "PaperTradeExperimentJournal",
        FailingExperimentJournal,
    )

    driver = create_configured_desktop_broker_driver(
        event_sink=lambda event: None,
        account_snapshot_sink=lambda snapshot: None,
        configuration_loader=lambda: configured,
        broker_runtime_factory=lambda **kwargs: BrokerRuntime(
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
        ),
        webull_broker_factory=lambda value: broker,
        webull_market_data_factory=lambda value: stream,
        clock=lambda: NOW,
    )

    decision_sink = captured["scanner_decision_sink"]
    price_observer = captured["scanner_adapter"]._price_observer

    assert decision_sink is not None
    assert price_observer is None

    # Experimental persistence is an optional PAPER/TEST sidecar.
    # Its failure must never own the scanner/runtime lifecycle.
    assert decision_sink(object()) is False
    assert decision_sink.metrics().rejected == 1
    assert decision_sink.close(timeout_seconds=1)

    assert driver._scanner is coordinator


def test_warrior_observer_uses_runtime_stream_and_isolated_probe_stream(monkeypatch) -> None:
    configured = replace(configuration(), warrior_forward_paper_enabled=True)
    broker = FakeBroker()
    stream = object()
    probe_stream = object()
    stream_factory_calls = []

    class Coordinator:
        def set_retained_channels_source(self, source):
            self.retained_source = source

    coordinator = Coordinator()
    captured = {}
    monkeypatch.setattr(
        desktop_broker_module, "create_desktop_scanner_infrastructure",
        lambda **kwargs: captured.update(kwargs) or SimpleNamespace(coordinator=coordinator),
    )

    class Observer:
        def __call__(self, event): pass
        def bind_scanner_adapter(self, value): self.adapter = value
        def retained_symbols(self): return ("XYZ",)

    observer = Observer()
    create_configured_desktop_broker_driver(
        event_sink=lambda event: None, account_snapshot_sink=lambda snapshot: None,
        configuration_loader=lambda: configured,
        broker_runtime_factory=lambda **kwargs: BrokerRuntime(
            provider="webull", capabilities=BrokerCapabilities(
                provider="webull", version="test", supports_execution=True,
                supports_account_data=True, supports_market_data=True,
                supports_streaming=True,
            ), execution=broker, market_data=stream,
        ),
        webull_broker_factory=lambda value: broker,
        webull_market_data_factory=(
            lambda value: stream_factory_calls.append(value) or probe_stream
        ),
        market_event_observer=observer, clock=lambda: NOW,
    )
    assert observer.adapter is captured["scanner_adapter"]
    assert coordinator.retained_source() == ("XYZ",)

    # Scanner ownership stays on the BrokerRuntime market-data stream.
    assert captured["market_data_client"] is stream

    # Startup capability probing intentionally owns one isolated stream so
    # probe subscriptions cannot mutate the scanner's live subscription set.
    assert stream_factory_calls == [configured]



def test_stale_scanner_recovery_reconnects_once_and_waits_for_fresh_data() -> None:
    configured = configuration()

    class Scanner:
        def __init__(self):
            self.calls = 0

        def recover_stream(self):
            self.calls += 1
            return ("quotes", "trades")

    scanner = Scanner()
    events = []

    driver = DesktopBrokerRuntimeDriver(
        configuration=configured,
        broker_runtime=BrokerRuntime(
            provider="webull",
            capabilities=BrokerCapabilities(
                provider="webull",
                version="test",
                supports_execution=True,
                supports_account_data=True,
                supports_market_data=True,
                supports_streaming=True,
            ),
            execution=FakeBroker(),
            market_data=object(),
        ),
        event_sink=events.append,
        account_snapshot_sink=lambda snapshot: None,
        scanner_coordinator=scanner,
        clock=lambda: NOW,
    )

    driver._recover_stale_scanner_stream(("AAA", "BBB"))

    assert scanner.calls == 1
    assert driver._stale_recovery_pending is True
    assert driver._stale_recovery_symbols == ("AAA", "BBB")

    event_types = [event.event_type for event in events]
    assert "MARKET_DATA_RECONNECTING" in event_types


def test_stale_scanner_recovery_cooldown_suppresses_reconnect_storm() -> None:
    configured = configuration()

    class Scanner:
        def __init__(self):
            self.calls = 0

        def recover_stream(self):
            self.calls += 1
            return ("quotes",)

    scanner = Scanner()

    driver = DesktopBrokerRuntimeDriver(
        configuration=configured,
        broker_runtime=BrokerRuntime(
            provider="webull",
            capabilities=BrokerCapabilities(
                provider="webull",
                version="test",
                supports_execution=True,
                supports_account_data=True,
                supports_market_data=True,
                supports_streaming=True,
            ),
            execution=FakeBroker(),
            market_data=object(),
        ),
        event_sink=lambda event: None,
        account_snapshot_sink=lambda snapshot: None,
        scanner_coordinator=scanner,
        clock=lambda: NOW,
    )

    driver._recover_stale_scanner_stream(("AAA",))

    assert scanner.calls == 1
    assert driver._stale_recovery_pending is True

    # Simulate a cooldown that has already expired. Recovery must still
    # remain single-flight until a fresh scanner snapshot confirms success.
    driver._last_stale_recovery_at = -1000000.0

    driver._recover_stale_scanner_stream(("AAA",))
    driver._recover_stale_scanner_stream(("AAA",))

    assert scanner.calls == 1
    assert driver._stale_recovery_pending is True



def test_recovery_confirmation_ignores_unrelated_stale_symbols() -> None:
    configured = configuration()
    events = []

    driver = DesktopBrokerRuntimeDriver(
        configuration=configured,
        broker_runtime=BrokerRuntime(
            provider="webull",
            capabilities=BrokerCapabilities(
                provider="webull",
                version="test",
                supports_execution=True,
                supports_account_data=True,
                supports_market_data=True,
                supports_streaming=True,
            ),
            execution=FakeBroker(),
            market_data=object(),
        ),
        event_sink=events.append,
        account_snapshot_sink=lambda snapshot: None,
        scanner_coordinator=None,
        clock=lambda: NOW,
    )

    driver._stale_recovery_pending = True
    driver._stale_recovery_symbols = ("AAA", "BBB")

    driver._observe_scanner_staleness(("XYZ",))

    assert driver._stale_recovery_pending is False
    assert driver._stale_recovery_symbols == ()
    assert "MARKET_DATA_RECONNECTED" in [
        event.event_type for event in events
    ]


def test_recovery_confirmation_waits_for_original_stale_symbol() -> None:
    configured = configuration()
    events = []

    driver = DesktopBrokerRuntimeDriver(
        configuration=configured,
        broker_runtime=BrokerRuntime(
            provider="webull",
            capabilities=BrokerCapabilities(
                provider="webull",
                version="test",
                supports_execution=True,
                supports_account_data=True,
                supports_market_data=True,
                supports_streaming=True,
            ),
            execution=FakeBroker(),
            market_data=object(),
        ),
        event_sink=events.append,
        account_snapshot_sink=lambda snapshot: None,
        scanner_coordinator=None,
        clock=lambda: NOW,
    )

    driver._stale_recovery_pending = True
    driver._stale_recovery_symbols = ("AAA", "BBB")

    driver._observe_scanner_staleness(("BBB", "XYZ"))
    assert driver._stale_recovery_pending is True
    assert driver._stale_recovery_symbols == ("AAA", "BBB")
    assert "MARKET_DATA_RECONNECTED" not in [
        event.event_type for event in events
    ]


def test_fresh_scanner_data_completes_pending_stale_recovery() -> None:
    configured = configuration()
    events = []

    driver = DesktopBrokerRuntimeDriver(
        configuration=configured,
        broker_runtime=BrokerRuntime(
            provider="webull",
            capabilities=BrokerCapabilities(
                provider="webull",
                version="test",
                supports_execution=True,
                supports_account_data=True,
                supports_market_data=True,
                supports_streaming=True,
            ),
            execution=FakeBroker(),
            market_data=object(),
        ),
        event_sink=events.append,
        account_snapshot_sink=lambda snapshot: None,
        scanner_coordinator=None,
        clock=lambda: NOW,
    )

    driver._stale_recovery_pending = True
    driver._stale_recovery_symbols = ("AAA",)

    driver._complete_stale_scanner_recovery()

    assert driver._stale_recovery_pending is False
    assert driver._stale_recovery_symbols == ()

    event_types = [event.event_type for event in events]
    assert "MARKET_DATA_RECONNECTED" in event_types


def test_failed_stale_scanner_recovery_clears_pending_state() -> None:
    configured = configuration()

    class Scanner:
        def recover_stream(self):
            raise RuntimeError("reconnect failed")

    events = []

    driver = DesktopBrokerRuntimeDriver(
        configuration=configured,
        broker_runtime=BrokerRuntime(
            provider="webull",
            capabilities=BrokerCapabilities(
                provider="webull",
                version="test",
                supports_execution=True,
                supports_account_data=True,
                supports_market_data=True,
                supports_streaming=True,
            ),
            execution=FakeBroker(),
            market_data=object(),
        ),
        event_sink=events.append,
        account_snapshot_sink=lambda snapshot: None,
        scanner_coordinator=Scanner(),
        clock=lambda: NOW,
    )

    driver._recover_stale_scanner_stream(("AAA",))

    assert driver._stale_recovery_pending is False

    event_types = [event.event_type for event in events]
    assert "MARKET_DATA_RECONNECTING" in event_types
    assert "scanner_error" in event_types


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
        "MARKET_DATA_DISABLED_BY_CONFIGURATION",
        "BROKER_DISCONNECTED",
    ]
    assert [event.health.broker_status for event in events] == [
        "CONNECTING",
        "CONNECTED",
        None,
        "DISCONNECTED",
    ]


def test_driver_owns_market_observer_start_and_stop_without_broker_mutation() -> None:
    class Observer:
        def __init__(self):
            self.calls = []
        def __call__(self, event):
            self.calls.append("event")
        def start(self, environment):
            self.calls.append(("start", environment))
        def stop(self):
            self.calls.append("stop")

    observer = Observer()
    broker = FakeBroker()
    stop_event = Event()
    stop_event.set()
    driver = DesktopBrokerRuntimeDriver(
        configuration=configuration(), broker_runtime=broker_runtime(broker),
        event_sink=lambda event: None, account_snapshot_sink=lambda snapshot: None,
        market_event_observer=observer, clock=lambda: NOW,
    )
    driver.run(stop_event=stop_event, cycle_sink=lambda cycle: None)
    assert observer.calls == [("start", "PAPER"), "stop"]
    assert not {"submit_order", "replace_order", "cancel_order"}.intersection(broker.calls)


def test_driver_preserves_primary_cleanup_error_and_logs_secondary(caplog) -> None:
    class DisconnectFailingBroker(FakeBroker):
        def disconnect(self) -> None:
            super().disconnect()
            raise OSError("secondary broker cleanup sentinel")

    class StopFailingObserver:
        def __call__(self, event) -> None:
            pass

        def start(self, environment: str) -> None:
            pass

        def stop(self) -> None:
            raise ValueError("primary observer cleanup sentinel")

    broker = DisconnectFailingBroker()
    stop_event = Event()
    stop_event.set()
    driver = DesktopBrokerRuntimeDriver(
        configuration=configuration(),
        broker_runtime=broker_runtime(broker),
        event_sink=lambda event: None,
        account_snapshot_sink=lambda snapshot: None,
        market_event_observer=StopFailingObserver(),
        clock=lambda: NOW,
    )

    with caplog.at_level("ERROR", logger="atlas.runtime"):
        with pytest.raises(
            ValueError,
            match="primary observer cleanup sentinel",
        ):
            driver.run(stop_event=stop_event, cycle_sink=lambda cycle: None)

    assert broker.calls == ["connect", "disconnect"]
    assert "lifecycle_phase=observer/Warrior sidecar stop" in caplog.text
    assert "exception_role=primary" in caplog.text
    assert "exception_type=ValueError" in caplog.text
    assert "lifecycle_phase=broker disconnect" in caplog.text
    assert "exception_role=secondary" in caplog.text
    assert "exception_type=OSError" in caplog.text


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
        ] == ["CONNECTING", "CONNECTED", "CONNECTED", "DISCONNECTED"]
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

def test_candidate_freshness_does_not_trigger_transport_recovery() -> None:
    """Fresh transport events must not reconnect for an old retained candidate."""

    class Cycle:
        events_read = 1

    class Scanner:
        def __init__(self) -> None:
            self.recover_calls = 0

        def run_available(self):
            stop_event.set()
            return Cycle()

        def snapshot(self):
            return SimpleNamespace(
                ranked_candidates=(),
                decisions=(),
                processed_events=1,
                ignored_events=0,
                active_symbols=("XYZ",),
            )

        def recover_stream(self):
            self.recover_calls += 1
            return ("XYZ",)

    class Publisher:
        def __init__(self) -> None:
            self.last_changed = False
            self.last_stale_symbols = ("XYZ",)

        def publish(self, snapshot, *, cycle, now):
            return self.last_stale_symbols

    stop_event = Event()
    scanner = Scanner()

    driver = object.__new__(DesktopBrokerRuntimeDriver)
    driver._scanner = scanner
    driver._scanner_publisher = Publisher()
    driver._scanner_events_since_observation = 0
    driver._cycles_completed = 0
    driver._market_data_stop = Event()
    driver._timestamp = lambda: NOW
    driver._scanner_log = lambda *args, **kwargs: None
    driver._publish_scanner_observation_if_due = lambda *args, **kwargs: None

    def unexpected_candidate_recovery(stale_symbols):
        raise AssertionError(
            "candidate/display freshness reached transport recovery: "
            f"{stale_symbols!r}"
        )

    driver._observe_scanner_staleness = unexpected_candidate_recovery
    driver._publish_terminal_market_data_failure = lambda exc: (_ for _ in ()).throw(exc)

    driver._receive_market_data(stop_event)

    assert scanner.recover_calls == 0
    assert driver._scanner_events_since_observation == 1
