from __future__ import annotations

from app.live_scanner.coordinator import LiveScannerCoordinator
from app.performance_diagnostics import PerformanceDiagnostics
from app.webull.websocket_client import OfficialSdkStreamBackend


class _Sdk:
    on_quotes_message = None
    on_connect_success = None
    on_disconnect = None

    def get_session_id(self):
        return "offline-session"

    def connect(self):
        return None

    def disconnect(self):
        return None

    def subscribe(self, *args):
        return None

    def unsubscribe(self, **kwargs):
        return None


class _Transport:
    def __init__(self):
        self.connected = False
        self.subscriptions = []

    def connect(self):
        self.connected = True

    def subscribe(self, channels):
        self.subscriptions.append(tuple(channels))

    def read_event_nowait(self):
        return None


class _Engine:
    subscription_symbols = ()

    def consume_transport_only(self, event):
        return event


def test_retained_symbols_do_not_turn_bootstrap_updates_into_replacements():
    transport = _Transport()
    coordinator = LiveScannerCoordinator(
        transport,
        _Engine(),
        retained_channels_source=lambda: ("POSITION", "ORDER"),
    )
    coordinator.connect()
    coordinator._channels = ("ORDER", "POSITION", "A")
    coordinator._subscription_bootstrap_pending = True
    for symbol in ("B", "C", "D"):
        coordinator._scanner_channels = (symbol,)
        coordinator._sync_subscription()
    assert transport.subscriptions == []
    coordinator._scanner_channels = tuple(f"S{i}" for i in range(100))
    coordinator._sync_subscription()
    assert len(transport.subscriptions) == 1
    assert len(transport.subscriptions[0]) <= 100


def test_subscription_state_exposes_authoritative_active_fingerprint():
    backend = OfficialSdkStreamBackend(
        _Sdk(), registration_grace_seconds=0, sdk_client_factory=_Sdk
    )
    backend.connect()
    backend.subscribe(("B", "A"))
    state = backend.subscription_state
    assert state["active_count"] == 2
    assert state["active_fingerprint"]
    assert state["generation"] == backend.generation_metrics["generation"]


def test_generation_accounting_reconciles_enqueue_and_dequeue():
    backend = OfficialSdkStreamBackend(_Sdk(), registration_grace_seconds=0)
    backend.connect()
    backend._on_quotes_message(backend.client, "quotes", {"symbol": "A"})
    backend.receive_nowait()
    accounting = backend.generation_accounting()[-1]
    assert accounting["callbacks_enqueued"] == 1
    assert accounting["callbacks_dequeued"] == 1
    assert accounting["callbacks_purged"] == 0


def test_ingestion_halted_is_received_but_not_enqueued():
    backend = OfficialSdkStreamBackend(_Sdk(), registration_grace_seconds=0)
    backend.connect()
    backend.halt_callback_ingestion()
    backend._on_quotes_message(backend.client, "quotes", {"symbol": "A"})
    accounting = backend.generation_accounting()[-1]
    assert accounting["callbacks_received"] == 1
    assert accounting["callbacks_rejected_ingestion_halted"] == 1
    assert accounting["callbacks_enqueued"] == 0


def test_overflow_is_received_but_not_enqueued():
    backend = OfficialSdkStreamBackend(_Sdk(), registration_grace_seconds=0, ingestion_capacity=1)
    backend.connect()
    backend._on_quotes_message(backend.client, "quotes", {"symbol": "A"})
    backend._on_quotes_message(backend.client, "quotes", {"symbol": "B"})
    accounting = backend.generation_accounting()[-1]
    assert accounting["callbacks_received"] == 2
    assert accounting["callbacks_rejected_overflow"] == 1
    assert accounting["callbacks_enqueued"] == 1


def test_retired_generation_pending_queue_is_purged_and_accounted():
    backend = OfficialSdkStreamBackend(
        _Sdk(), registration_grace_seconds=0, sdk_client_factory=_Sdk
    )
    backend.connect()
    backend._on_quotes_message(backend.client, "quotes", {"symbol": "A"})
    backend.connect()
    summary = [item for item in backend.generation_accounting() if item["generation"] == 1][0]
    assert summary["callbacks_enqueued"] == 1
    assert summary["callbacks_dequeued_total"] == 0
    assert summary["callbacks_purged"] == 1
    assert summary["retirement_disposition"] == "PURGED"


def test_generation_accounting_reports_separate_domain_statuses():
    backend = OfficialSdkStreamBackend(_Sdk(), registration_grace_seconds=0)
    backend.connect()
    backend._on_quotes_message(backend.client, "quotes", {"symbol": "A"})
    backend.receive_nowait()
    accounting = backend.generation_accounting()[-1]
    assert accounting["received_domain_status"] == "ACCOUNTING_COMPLETE"
    assert accounting["queue_domain_status"] == "ACCOUNTING_COMPLETE"
    assert len(backend.generation_accounting()) <= 16


def test_cross_generation_startup_metric_is_explicit():
    backend = OfficialSdkStreamBackend(_Sdk(), registration_grace_seconds=0)
    backend.connect()
    backend._on_quotes_message(backend.client, "quotes", {"symbol": "A"})
    backend.receive_nowait()
    metrics = backend.generation_metrics
    assert metrics["first_callback_generation"] == metrics["first_dequeue_generation"]
    assert metrics["current_generation_callback_to_dequeue_ms"] is not None


def test_diagnostic_slow_component_evidence_is_bounded_and_timestamped():
    diagnostics = PerformanceDiagnostics()
    captured = []
    diagnostics.set_diagnostic_sink(lambda kind, payload: captured.append((kind, payload)))
    diagnostics.record_component_duration(
        "warrior.gui_snapshot_lock_wait", 500.0, event_type="GUI_REFRESH",
        slow_threshold_ms=250.0,
    )
    slow = [payload for kind, payload in captured if kind == "slow_component"]
    assert slow
    assert slow[-1]["timestamp"]
