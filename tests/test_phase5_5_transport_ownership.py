from __future__ import annotations

from app.live_scanner.coordinator import LiveScannerCoordinator
from app.webull.websocket_client import OfficialSdkStreamBackend


class _Sdk:
    on_quotes_message = None
    on_connect_success = None
    on_disconnect = None

    def __init__(self, session: str = "offline"):
        self.session = session

    def get_session_id(self):
        return self.session

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
        self.subscriptions: list[tuple[str, ...]] = []

    def connect(self):
        self.connected = True

    def subscribe(self, channels):
        self.subscriptions.append(tuple(channels))


class _Engine:
    subscription_symbols = ()


def _backend():
    sequence = iter(range(20))

    def factory():
        return _Sdk(f"session-{next(sequence)}")

    return OfficialSdkStreamBackend(
        _Sdk("session-0"),
        registration_grace_seconds=0,
        sdk_client_factory=factory,
        generation_allocator=iter(range(1, 32)).__next__,
    )


def test_direct_bootstrap_subscribe_does_not_replace_active_set():
    transport = _Transport()
    coordinator = LiveScannerCoordinator(transport, _Engine())
    coordinator.connect()
    coordinator.subscribe(("A",))
    for symbol in ("B", "C", "D", "E"):
        coordinator.subscribe((symbol,))
    assert transport.subscriptions == [("A",)]
    stable = tuple(f"S{i}" for i in range(100))
    coordinator.subscribe(stable)
    assert transport.subscriptions[-1] == stable
    assert len(transport.subscriptions) == 2


def test_generation_replacement_purges_old_queue_and_records_disposition():
    backend = _backend()
    backend.connect()
    old_client = backend.client
    backend._on_quotes_message(old_client, "quotes", {"symbol": "A"})
    assert backend.memory_metrics()["current_depth"] == 1

    backend.connect()
    summary = [item for item in backend.generation_accounting() if item["generation"] == 1][0]
    assert summary["callbacks_enqueued"] == 1
    assert summary["callbacks_purged"] == 1
    assert summary["callbacks_dequeued_total"] == 0
    assert summary["retirement_disposition"] == "PURGED"
    assert backend.memory_metrics()["current_depth"] == 0


def test_retired_client_callback_cannot_enter_current_generation_queue():
    backend = _backend()
    backend.connect()
    old_client = backend.client
    backend.connect()
    old_client.on_quotes_message(old_client, "quotes", {"symbol": "OLD"})
    assert backend.memory_metrics()["current_depth"] == 0
    assert backend.generation_metrics["generation"] == 2


def test_generation_summary_retention_covers_ten_generations():
    backend = _backend()
    for _ in range(10):
        backend.connect()
    summaries = backend.generation_accounting()
    assert len(summaries) >= 10
    assert {item["generation"] for item in summaries} >= set(range(1, 10))
    assert all(item["retirement_disposition"] in {"PURGED", "DRAINED", "NO_PENDING_CALLBACKS"} for item in summaries[:-1])


def test_queue_accounting_domains_remain_non_overlapping_after_purge():
    backend = _backend()
    backend.connect()
    backend._on_quotes_message(backend.client, "quotes", {"symbol": "A"})
    backend._on_quotes_message(backend.client, "quotes", {"symbol": "B"})
    backend.connect()
    summary = [item for item in backend.generation_accounting() if item["generation"] == 1][0]
    assert summary["callbacks_received"] == 2
    assert summary["callbacks_enqueued"] == 2
    assert summary["callbacks_dequeued_total"] == 0
    assert summary["callbacks_purged"] == 2
    assert summary["queue_domain_status"] == "ACCOUNTING_COMPLETE"
