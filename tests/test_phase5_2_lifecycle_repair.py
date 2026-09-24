from __future__ import annotations

from datetime import UTC, datetime

from app.live_scanner.coordinator import LiveScannerCoordinator
from app.live_scanner.models import LiveScannerCycle
from app.performance_diagnostics import PerformanceDiagnostics
from app.webull.websocket_client import OfficialSdkStreamBackend, WebullWebSocketClient


class _Transport:
    heartbeat_ok = True
    subscription_acknowledged = True

    def __init__(self, events=()):
        self.events = list(events)
        self.connected = False

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def subscribe(self, channels):
        return None

    def read_event_nowait(self):
        return self.events.pop(0) if self.events else None

    def read_event(self):
        return self.read_event_nowait()


class _Engine:
    def __init__(self):
        self.reduced = []

    def consume_transport_only(self, event):
        self.reduced.append(event)
        return True


def test_startup_transport_drain_does_not_admit_scanner_or_entry_authority():
    events = [object(), object(), object()]
    transport = _Transport(events)
    engine = _Engine()
    observed = []
    coordinator = LiveScannerCoordinator(
        transport,
        engine,
        event_observer=observed.append,
        maximum_events_per_cycle=10,
    )
    coordinator.connect()
    assert coordinator.running is False

    cycle = coordinator.run_transport_available()

    assert cycle.events_read == 3
    assert engine.reduced == events
    assert observed == events
    assert coordinator.running is False
    assert cycle.decisions_created == 0


class _Sdk:
    def __init__(self):
        self.on_quotes_message = None
        self.on_connect_success = None
        self.on_disconnect = None

    def get_session_id(self):
        return "session"

    def connect(self):
        return None

    def disconnect(self):
        return None

    def subscribe(self, *channels):
        return None

    def unsubscribe(self, **kwargs):
        return None


def test_generation_allocator_survives_backend_recreation():
    value = iter((11, 12)).__next__
    first = OfficialSdkStreamBackend(_Sdk(), registration_grace_seconds=0, generation_allocator=value)
    second = OfficialSdkStreamBackend(_Sdk(), registration_grace_seconds=0, generation_allocator=value)
    first.connect()
    second.connect()
    assert first.generation_metrics["generation"] == 11
    assert second.generation_metrics["generation"] == 12


def test_recovery_claims_are_coalesced_without_second_owner():
    client = WebullWebSocketClient(
        object(), object(), object(), lambda _seconds: None, object()
    )
    assert client.begin_recovery("WATCHDOG") is True
    assert client.begin_recovery("TRANSPORT") is False
    assert client.recovery_active is True
    client.end_recovery("WATCHDOG")
    assert client.recovery_active is False


def test_radar_promotions_do_not_evict_critical_lifecycle_evidence():
    diagnostics = PerformanceDiagnostics()
    diagnostics.record_stream_observability("PAYLOAD_STALE_CONTEXT", generation=3)
    diagnostics.record_stream_observability("RECOVERY_STARTED", controller="WATCHDOG")
    for index in range(500):
        diagnostics.record_stream_observability("RADAR_PROMOTED", symbol=f"S{index}")
    evidence = diagnostics.stream_observability()
    names = {event["event"] for event in evidence["events"]}
    assert "PAYLOAD_STALE_CONTEXT" in names
    assert "RECOVERY_STARTED" in names
    assert len(evidence["events"]) <= 512
    assert len(evidence["radar_events"]) <= 128
