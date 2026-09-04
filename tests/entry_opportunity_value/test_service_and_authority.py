from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event
from time import perf_counter

from app.entry_opportunity_value import EntryOpportunityValueService, evaluate_entry_opportunity
from tests.entry_opportunity_value.test_evaluator import CUTOFF, entry_context


D = Decimal


class MemoryStore:
    def __init__(self):
        self.items = []
        self.closed = False

    def append(self, observation):
        self.items.append(observation)

    def close(self):
        self.closed = True


class FailingStore:
    def append(self, observation):
        raise OSError("research disk unavailable")

    def close(self):
        pass


class BlockingStore(MemoryStore):
    def __init__(self):
        super().__init__()
        self.entered = Event()
        self.release = Event()

    def append(self, observation):
        self.entered.set()
        self.release.wait(2)
        super().append(observation)


def test_worker_completes_and_tracks_action_metrics_off_thread():
    store = MemoryStore()
    service = EntryOpportunityValueService(store, clock=lambda: CUTOFF)
    assert service.observe(entry_context()) is True
    assert service.close(timeout_seconds=2) is True
    metrics = service.metrics()
    assert metrics.observations_accepted == 1
    assert metrics.observations_completed == 1
    assert metrics.outstanding == 0
    assert sum(count for _, count in metrics.classification_counts) == 1
    assert store.closed is True


def test_persistence_failure_is_counted_and_never_reaches_producer():
    service = EntryOpportunityValueService(FailingStore(), clock=lambda: CUTOFF)
    assert service.observe(entry_context()) is True
    assert service.close(timeout_seconds=2) is True
    metrics = service.metrics()
    assert metrics.failures == 1
    assert metrics.observations_completed == 0


def test_queue_pressure_is_bounded_and_recoverable_research_loss():
    store = BlockingStore()
    service = EntryOpportunityValueService(store, capacity=1, clock=lambda: CUTOFF)
    assert service.observe(entry_context()) is True
    assert store.entered.wait(1)
    assert service.observe(entry_context(symbol="TWO", lifecycle_id="two")) is True
    assert service.observe(entry_context(symbol="THREE", lifecycle_id="three")) is False
    assert service.metrics().queue_high_water <= 1
    assert service.metrics().rejections == 1
    store.release.set()
    assert service.close(timeout_seconds=2) is True


def test_high_volume_pure_evaluation_is_bounded():
    contexts = tuple(
        entry_context(symbol=f"S{index}", lifecycle_id=f"lifecycle-{index}")
        for index in range(5000)
    )
    started = perf_counter()
    observations = tuple(evaluate_entry_opportunity(item) for item in contexts)
    elapsed = perf_counter() - started
    assert len(observations) == 5000
    assert elapsed < 5.0


def test_high_volume_admission_remains_nonblocking_under_worker_backlog():
    store = BlockingStore()
    service = EntryOpportunityValueService(store, capacity=5000, clock=lambda: CUTOFF)
    assert service.observe(entry_context(symbol="S0", lifecycle_id="lifecycle-0"))
    assert store.entered.wait(1)
    started = perf_counter()
    accepted = sum(
        service.observe(entry_context(symbol=f"S{index}", lifecycle_id=f"lifecycle-{index}"))
        for index in range(1, 5000)
    )
    admission_elapsed = perf_counter() - started
    assert accepted == 4999
    assert admission_elapsed < 2.0
    assert service.metrics().queue_high_water <= 5000
    store.release.set()
    assert service.close(timeout_seconds=5)


def test_package_has_no_execution_authority_imports_or_calls():
    forbidden_import_fragments = {
        "broker", "paper_gateway", "paper_trading", "order_placement",
        "order_cancellation", "execution", "account", "risk", "scanner",
        "warrior_momentum", "configuration", "market_data", "webull",
        "trade_intelligence", "opportunity_discovery",
    }
    forbidden_calls = {
        "place_order", "submit_order", "submit_entry", "submit_exit",
        "cancel_order", "replace_order", "authorize_order", "resize_position",
        "close_position", "modify_order", "veto_order",
        "override_risk", "change_quantity",
    }
    for path in Path("app/entry_opportunity_value").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = []
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    calls.append(node.func.attr)
                elif isinstance(node.func, ast.Name):
                    calls.append(node.func.id)
        assert not any(
            fragment in module.lower()
            for module in imports
            for fragment in forbidden_import_fragments
        ), (path, imports)
        assert forbidden_calls.isdisjoint(calls), (path, calls)


def test_service_trend_uses_only_prior_decision_observation():
    store = MemoryStore()
    times = iter((CUTOFF, CUTOFF, CUTOFF + timedelta(seconds=1), CUTOFF + timedelta(seconds=1)))
    service = EntryOpportunityValueService(store, clock=lambda: next(times))
    assert service.observe(entry_context(technical_confidence=D("60")))
    later = entry_context(
        decision_cutoff=CUTOFF + timedelta(seconds=1),
        quote_timestamp=CUTOFF + timedelta(milliseconds=900),
        quote_received_at=CUTOFF + timedelta(milliseconds=950),
        bid=D("10.19"),
        ask=D("10.20"),
        technical_confidence=D("70"),
    )
    assert service.observe(later)
    assert service.close(timeout_seconds=2)
    assert len(store.items) == 2
