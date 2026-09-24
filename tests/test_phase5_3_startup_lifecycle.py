from __future__ import annotations

from datetime import UTC, datetime
from threading import Event, Lock

import pytest

from app.live_scanner.coordinator import LiveScannerCoordinator
from app.performance_diagnostics import PerformanceDiagnostics
from app.services.runtime_drivers.broker import DesktopBrokerRuntimeDriver
from app.webull.websocket_client import OfficialSdkStreamBackend


class _Transport:
    def __init__(self) -> None:
        self.connected = False
        self.subscriptions: list[tuple[str, ...]] = []
        self.disconnect_timeouts: list[float | None] = []

    def connect(self) -> None:
        self.connected = True

    def disconnect(self, *, timeout_seconds: float | None = None) -> None:
        self.disconnect_timeouts.append(timeout_seconds)
        self.connected = False

    def subscribe(self, channels: tuple[str, ...]) -> None:
        self.subscriptions.append(tuple(channels))

    def read_event(self):
        return None

    def read_event_nowait(self):
        return None


class _Engine:
    active_symbols = ()
    subscription_symbols = ()

    def prepare_universe(self, _asset_classes):
        return ("A",)

    def consume_transport_only(self, event):
        return event

    def refresh_universe(self, *_args, **_kwargs):
        return self.subscription_symbols


def test_startup_production_path_connects_before_consumer_creation():
    order: list[str] = []

    class Scanner:
        connected = False
        running = False
        qualification_ready = False

        def connect(self):
            order.append("connect")
            self.connected = True

        def run_available(self):
            order.append("run_available")

        def start(self, **_kwargs):
            order.append("scanner_start")
            self.running = True
            return ()

        def set_readiness_observer(self, _observer):
            return None

        def snapshot(self):
            return type("Snapshot", (), {"active_symbols": (), "universe_size": 0, "eligible_symbol_count": 0})()

        pending_reference_symbols = ()
        channels = ()
        retained_channels = ()

    driver = object.__new__(DesktopBrokerRuntimeDriver)
    driver._scanner = Scanner()
    driver._market_data = object()
    driver._market_data_probe = None
    driver._startup_validation = type("Validation", (), {"scanner_ready": True})()
    driver._market_data_stop = Event()
    driver._publish_health = lambda *_args, **_kwargs: None
    driver._start_market_data_consumer = lambda _stop: order.append("consumer")
    driver._start_scanner = lambda: order.append("start_scanner") or False
    driver._start_dynamic_momentum_discovery = lambda: None
    driver._configuration = object()
    driver._market_data_connected = False
    driver._cycles_completed = 0
    driver._event_lock = Lock()
    driver._sequence = 0
    driver._emit = lambda *_args, **_kwargs: None
    driver._clock = lambda: datetime.now(UTC)
    driver._source = "test"
    driver._start_market_data(Event())

    assert order.index("connect") < order.index("consumer")


def test_transport_drain_is_available_before_scanner_readiness():
    transport = _Transport()
    engine = _Engine()
    coordinator = LiveScannerCoordinator(transport, engine, maximum_events_per_cycle=8)
    coordinator.connect()
    assert coordinator.running is False
    # The coordinator is connected, so a production consumer can call the
    # transport-only path without enabling scanner decisions.
    assert coordinator.run_transport_available().decisions_created == 0


def test_provisional_bootstrap_symbols_do_not_replace_active_stream():
    transport = _Transport()
    coordinator = LiveScannerCoordinator(transport, _Engine())
    coordinator.connect()
    coordinator._channels = ("A",)
    coordinator._scanner_channels = ("A",)
    coordinator._subscription_bootstrap_pending = True
    for symbol in ("B", "C", "D", "E"):
        coordinator._scanner_channels = (symbol,)
        coordinator._sync_subscription()
    assert transport.subscriptions == []
    coordinator._scanner_channels = tuple(f"S{i}" for i in range(100))
    coordinator._sync_subscription()
    assert transport.subscriptions == [tuple(f"S{i}" for i in range(100))]


def test_recovery_uses_one_composed_bounded_disconnect_window():
    transport = _Transport()
    coordinator = LiveScannerCoordinator(transport, _Engine())
    coordinator.connect()
    coordinator._channels = ("A",)
    coordinator._scanner_channels = ("A",)
    coordinator._running = True
    coordinator.recover_stream()
    assert transport.disconnect_timeouts == [15.0]


def test_recovery_timeout_remains_fail_closed():
    class TimeoutTransport(_Transport):
        def disconnect(self, *, timeout_seconds=None):
            self.disconnect_timeouts.append(timeout_seconds)
            raise TimeoutError("bounded disconnect timeout")

    coordinator = LiveScannerCoordinator(TimeoutTransport(), _Engine())
    coordinator.connect()
    coordinator._channels = ("A",)
    coordinator._scanner_channels = ("A",)
    coordinator._running = True
    with pytest.raises(TimeoutError):
        coordinator.recover_stream()
    assert coordinator.connected is False
    assert coordinator.running is False


class _Sdk:
    on_quotes_message = None
    on_connect_success = None
    on_disconnect = None

    def get_session_id(self):
        return "session"

    def connect(self):
        return None

    def disconnect(self):
        return None

    def subscribe(self, *args):
        return None

    def unsubscribe(self, **kwargs):
        return None


def test_connect_requested_uses_allocator_generation():
    diagnostics = PerformanceDiagnostics()
    import app.webull.websocket_client as websocket_client

    previous = websocket_client.performance_diagnostics
    websocket_client.performance_diagnostics = diagnostics
    try:
        backend = OfficialSdkStreamBackend(
            _Sdk(), registration_grace_seconds=0,
            generation_allocator=iter((7,)).__next__,
        )
        backend.connect()
        events = diagnostics.stream_observability()["events"]
        assert [e["event"] for e in events if e["event"] in {"CONNECT_REQUESTED", "CONNECT_STARTED"}] == [
            "CONNECT_REQUESTED", "CONNECT_STARTED"
        ]
        assert all(e["generation"] == 7 for e in events if e["event"] in {"CONNECT_REQUESTED", "CONNECT_STARTED"})
    finally:
        websocket_client.performance_diagnostics = previous


def test_purge_accounting_and_radar_snapshot_are_bounded():
    diagnostics = PerformanceDiagnostics()
    for index in range(500):
        diagnostics.record_stream_observability("RADAR_PROMOTED", symbol=f"S{index}")
    snapshot = diagnostics.snapshot().stream_observability
    assert len(snapshot["radar_events"]) <= 128
    assert snapshot["radar_events"]


def test_disconnect_purge_has_explicit_terminal_accounting():
    backend = OfficialSdkStreamBackend(_Sdk(), registration_grace_seconds=0)
    backend._on_quotes_message(backend.client, "quotes", {"symbol": "A"})
    assert backend.memory_metrics()["current_depth"] == 1
    backend.halt_callback_ingestion()
    metrics = backend.discard_metrics()
    assert metrics["messages_purged_on_disconnect"] == 1
    assert backend.memory_metrics()["current_depth"] == 0
