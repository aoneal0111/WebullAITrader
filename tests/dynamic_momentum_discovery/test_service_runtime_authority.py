from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
from threading import Event

from app.dynamic_momentum_discovery import (
    DynamicMomentumDiscoveryRunner,
    DynamicMomentumDiscoveryService,
    JsonLinesDiscoveryStore,
    WebullBroadDiscoveryProvider,
    evaluate_dynamic_momentum,
)
from tests.dynamic_momentum_discovery.helpers import NOW, snapshot
from tests.dynamic_momentum_discovery.test_provider_and_breadth import PagedScreener


class MemoryStore:
    def __init__(self, *, fail=False):
        self.items = []
        self.fail = fail
        self.closed = False

    def append(self, item):
        if self.fail:
            raise OSError("research persistence unavailable")
        self.items.append(item)

    def close(self):
        self.closed = True


def test_disabled_has_no_worker_persistence_or_runtime_effect(tmp_path):
    path = tmp_path / "disabled.jsonl"
    service = DynamicMomentumDiscoveryService(
        JsonLinesDiscoveryStore(path), enabled=False
    )
    assert service.observe(snapshot()) is False
    assert service.close()
    assert not path.exists()
    assert service.metrics().enabled is False


def test_10000_unchanged_updates_create_one_bounded_episode():
    store = MemoryStore()
    service = DynamicMomentumDiscoveryService(store, capacity=16)
    for _ in range(10_000):
        service.observe(snapshot())
    assert service.close()
    metrics = service.metrics()
    assert metrics.accepted == 1
    assert metrics.suppressed == 9_999
    assert metrics.completed == 1
    assert len(store.items) == 1


def test_meaningful_state_change_creates_new_observation():
    store = MemoryStore()
    service = DynamicMomentumDiscoveryService(store)
    assert service.observe(snapshot())
    assert service.observe(snapshot(price=snapshot().price + 1))
    assert service.close()
    assert service.metrics().accepted == 2
    assert len(store.items) == 2


def test_queue_full_is_nonblocking_and_isolated():
    entered = Event()
    release = Event()

    def blocked_evaluator(value, **kwargs):
        entered.set()
        release.wait(2)
        return evaluate_dynamic_momentum(value, **kwargs)

    service = DynamicMomentumDiscoveryService(
        MemoryStore(), capacity=1, evaluator=blocked_evaluator
    )
    assert service.observe(snapshot(symbol="ONE"))
    assert entered.wait(1)
    assert service.observe(snapshot(symbol="TWO"))
    assert service.observe(snapshot(symbol="THREE")) is False
    release.set()
    assert service.close()
    assert service.metrics().rejected >= 1


def test_persistence_failure_isolated_and_outstanding_drains():
    service = DynamicMomentumDiscoveryService(MemoryStore(fail=True))
    assert service.observe(snapshot())
    assert service.close()
    metrics = service.metrics()
    assert metrics.failed == 1
    assert metrics.completed == 1
    assert metrics.outstanding == 0


def test_retention_and_estimated_memory_are_bounded():
    service = DynamicMomentumDiscoveryService(
        MemoryStore(), maximum_retained_symbols=10, capacity=128
    )
    for index in range(100):
        assert service.observe(snapshot(symbol=f"S{index:03d}"))
    assert service.close()
    assert service.metrics().retained_symbols == 10
    assert service.estimated_retained_bytes() <= 10 * 2048 + 10 * 256


def test_jsonl_is_append_only_and_zero_authority(tmp_path):
    path = tmp_path / "dynamic.jsonl"
    service = DynamicMomentumDiscoveryService(JsonLinesDiscoveryStore(path))
    assert service.observe(snapshot())
    assert service.close()
    payload = json.loads(path.read_text(encoding="utf-8").strip())
    assert payload["research_only"] is True
    assert payload["production_promoted"] is False
    assert payload["selection_authorized"] is False
    assert payload["execution_authorized"] is False


def test_explicit_runner_is_research_only_and_does_not_promote_production():
    store = MemoryStore()
    service = DynamicMomentumDiscoveryService(store)
    runner = DynamicMomentumDiscoveryRunner(
        WebullBroadDiscoveryProvider(PagedScreener()), service
    )
    result = runner.collect(
        breadth_per_source=100, observed_at=NOW, session="REGULAR"
    )
    assert result.research_only is True
    assert result.production_universe_mutated is False
    assert result.execution_authorized is False
    assert result.assembled_symbols == 125
    assert service.close()


def test_provider_failure_does_not_escape_runner_or_other_source():
    service = DynamicMomentumDiscoveryService(MemoryStore())
    result = DynamicMomentumDiscoveryRunner(
        WebullBroadDiscoveryProvider(PagedScreener(fail_source="GAINERS")), service
    ).collect(breadth_per_source=50, observed_at=NOW, session="REGULAR")
    assert result.failure_type is None
    assert result.refresh is not None
    assert len(result.refresh.failures) == 1
    assert result.assembled_symbols == 50
    service.close()


def test_static_authority_and_one_way_composition_isolation():
    root = Path(__file__).parents[2]
    package = root / "app" / "dynamic_momentum_discovery"
    text = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    forbidden = (
        "app.realtime_scanner", "app.momentum_scanner", "app.paper_gateway",
        "app.live_execution", "app.strategies", "place_order", "submit_order",
        "cancel_order", "replace_order", "authorize_order", "resize_position",
        "close_position", "risk_override",
    )
    assert all(value not in text for value in forbidden)
    composition = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "app" / "composition").glob("*.py")
    )
    broker_composition = (
        root / "app" / "composition" / "desktop_broker_runtime.py"
    ).read_text(encoding="utf-8")
    assert "DynamicMomentumDiscoveryRuntime" in broker_composition
    assert "dynamic_momentum_discovery_runtime=" in broker_composition
    forbidden_consumers = (
        "shadow_promote_to_full_analysis", "production_promoted",
        ".place_order(", ".cancel_order(", ".replace_order(",
    )
    assert all(value not in composition for value in forbidden_consumers)


def test_producer_latency_is_measured_and_bounded_for_duplicates():
    service = DynamicMomentumDiscoveryService(MemoryStore())
    for _ in range(10_000):
        service.observe(snapshot())
    service.close()
    assert service.metrics().maximum_producer_latency_ms < 25
