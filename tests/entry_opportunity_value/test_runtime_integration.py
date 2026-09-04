from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import enumerate as enumerate_threads
from types import SimpleNamespace
from time import perf_counter

from app.entry_opportunity_value import (
    EntryOpportunityValueService,
    EntryOpportunityValueRuntimeObserver,
    ShadowAction,
)
from app.entry_opportunity_value.service import ShadowServiceMetrics


D = Decimal
CUTOFF = datetime(2026, 9, 3, 20, 9, 1, 281000, tzinfo=UTC)


class _MemoryStore:
    def __init__(self):
        self.items = []
        self.closed = False

    def append(self, item):
        self.items.append(item)

    def close(self):
        self.closed = True


class _InlineService:
    def __init__(self, *_args, **_kwargs):
        self.contexts = []
        self.closed = False

    def observe(self, context):
        self.contexts.append(context)
        return True

    def close(self, *, timeout_seconds=5):
        self.closed = True
        return True

    def metrics(self):
        count = len(self.contexts)
        return ShadowServiceMetrics(
            0, 1 if count else 0, count, count, 0, 0, 0, 0.0,
            tuple((action, 0) for action in ShadowAction),
            not self.closed, self.closed,
        )


class _RejectingService(_InlineService):
    def __init__(self, *_args, **_kwargs):
        super().__init__()
        self.rejected = 0

    def observe(self, context):
        self.rejected += 1
        return False

    def metrics(self):
        return ShadowServiceMetrics(
            0, 0, 0, 0, self.rejected, 0, 0, 0.0,
            tuple((action, 0) for action in ShadowAction), True, False,
        )


def _decision(*, lifecycle="WARRIOR_MOMENTUM_V1|CHPT|episode", **changes):
    observation = SimpleNamespace(
        bid=D("9.20"), ask=D("9.25"), price=D("9.22"),
        quote_received_timestamp=CUTOFF - timedelta(milliseconds=10),
    )
    value = SimpleNamespace(
        observation=observation,
        evaluation_timestamp=CUTOFF,
        quote_observed_at=CUTOFF - timedelta(milliseconds=20),
        best_bid_size=D("800"), best_ask_size=D("600"),
        scanner_rank=3, scanner_score=65,
        quote_provenance="SHARED_SCANNER_ADAPTER",
    )
    candidate = SimpleNamespace(
        symbol="CHPT", timestamp=CUTOFF, session="AFTER_HOURS",
        percentage_change=D("12.5"), relative_volume=D("7.2"),
        score=SimpleNamespace(total=D("65")),
        status=SimpleNamespace(value="ENTRY_READY"),
    )
    signal = SimpleNamespace(
        timestamp=CUTOFF, strategy_id="WARRIOR_MOMENTUM_V1",
        setup_type=SimpleNamespace(value="HIGH_OF_DAY_BREAKOUT"),
        entry_trigger=D("9.104550"), stop_price=D("9.05"),
    )
    result = dict(
        value=value, candidate=candidate, signal=signal,
        planned_quantity=1833, decision_state="AUTHORIZED",
        lifecycle_id=lifecycle,
    )
    result.update(changes)
    return result


def _observer(tmp_path, *, enabled=True, service_factory=_InlineService, **changes):
    values = dict(
        enabled=enabled, environment="PAPER",
        path=tmp_path / "eov.jsonl", capacity=32,
        clock=lambda: CUTOFF,
        service_factory=service_factory,
        store_factory=lambda _path: _MemoryStore(),
        research_context_source=lambda *_args: {
            "observed_at": CUTOFF - timedelta(milliseconds=30),
            "opportunity_id": "normalized-opportunity-1",
            "detector_memberships": (
                "HIGH_OF_DAY_BREAKOUT", "HIGHER_LOW_CONTINUATION",
            ),
            "trade_intelligence_experience_id": "experience-1",
        },
        order_correlation_source=lambda _lifecycle: {
            "order_id": "paper-order-1", "client_order_id": "client-1",
        },
    )
    values.update(changes)
    return EntryOpportunityValueRuntimeObserver(**values)


def test_disabled_creates_no_worker_or_evaluation(tmp_path):
    calls = []
    observer = _observer(
        tmp_path, enabled=False,
        service_factory=lambda *_args, **_kwargs: calls.append(True),
    )
    observer.start("PAPER")
    observer.observe_decision(**_decision())
    assert calls == []
    metrics = observer.metrics()
    assert metrics.enabled is False and metrics.accepted == 0


def test_chpt_replay_uses_authoritative_quote_and_is_research_only(tmp_path):
    store = _MemoryStore()
    observer = _observer(
        tmp_path,
        store_factory=lambda _path: store,
        service_factory=EntryOpportunityValueService,
    )
    observer.start("PAPER")
    observer.observe_decision(**_decision())
    assert observer.close(timeout_seconds=2)
    assert len(store.items) == 1
    result = store.items[0]
    assert result.features.price_drift_in_r == D("0.145450") / D("0.054550")
    assert result.features.current_entry_risk_multiple_vs_original == D("0.20") / D("0.054550")
    assert result.features.current_risk_budget_quantity == 499
    assert result.features.original_limit_marketable is False
    assert result.shadow_action is ShadowAction.INSUFFICIENT_EVIDENCE
    assert result.research_only is True and result.execution_authorized is False
    assert result.context.order_id == "paper-order-1"
    assert result.context.trade_intelligence_experience_id == "experience-1"
    assert result.context.detector_memberships == (
        "HIGHER_LOW_CONTINUATION", "HIGH_OF_DAY_BREAKOUT",
    )


def test_duplicate_cardinality_is_episode_bounded(tmp_path):
    observer = _observer(tmp_path)
    observer.start("PAPER")
    started = perf_counter()
    for _ in range(10_000):
        observer.observe_decision(**_decision())
    elapsed = perf_counter() - started
    metrics = observer.metrics()
    assert metrics.accepted == 1
    assert metrics.suppressed == 9_999
    assert metrics.episode_count == 1
    assert elapsed < 2.0
    assert metrics.producer_assembly_max_ms < 100.0
    assert observer.close()


def test_meaningful_transition_and_new_lifecycle_are_admitted(tmp_path):
    observer = _observer(tmp_path)
    observer.start("PAPER")
    observer.observe_decision(**_decision())
    observer.observe_decision(**_decision(decision_state="WORKING_ORDER_EXISTS"))
    observer.observe_decision(**_decision())
    observer.observe_decision(**_decision(
        lifecycle="WARRIOR_MOMENTUM_V1|CHPT|new-episode",
    ))
    metrics = observer.metrics()
    assert metrics.accepted == 4
    assert metrics.episode_count == 2
    assert observer.close()


def test_missing_and_stale_quote_are_preserved_honestly(tmp_path):
    store = _MemoryStore()
    observer = _observer(
        tmp_path, store_factory=lambda _path: store,
        service_factory=EntryOpportunityValueService,
    )
    observer.start("PAPER")
    missing = _decision()
    missing["value"].observation.bid = None
    missing["value"].observation.ask = None
    missing["value"].quote_observed_at = None
    observer.observe_decision(**missing)
    stale = _decision(lifecycle_id="WARRIOR_MOMENTUM_V1|CHPT|stale")
    stale["value"].quote_observed_at = CUTOFF - timedelta(seconds=6)
    observer.observe_decision(**stale)
    assert observer.close()
    assert store.items[0].features.absolute_price_drift is None
    assert store.items[1].features.quote_age_ms == D("6000.000")
    assert all(
        item.shadow_action is ShadowAction.INSUFFICIENT_EVIDENCE
        for item in store.items
    )


def test_future_quote_and_future_research_context_are_not_admitted(tmp_path):
    observer = _observer(tmp_path)
    observer.start("PAPER")
    future = _decision()
    future["value"].quote_observed_at = CUTOFF + timedelta(microseconds=1)
    observer.observe_decision(**future)
    assert observer.metrics().accepted == 0
    assert observer.metrics().failed == 1
    assert observer.close()


def test_invalid_path_and_factory_failure_are_isolated(tmp_path):
    invalid = _observer(tmp_path, path=tmp_path)
    invalid.start("PAPER")
    metrics = invalid.metrics()
    assert metrics.enabled and not metrics.healthy
    assert metrics.last_error_type == "ValueError"

    broken = _observer(
        tmp_path,
        service_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("research unavailable")
        ),
    )
    broken.start("PAPER")
    assert not broken.metrics().healthy
    broken.observe_decision(**_decision())
    assert broken.metrics().accepted == 0


def test_queue_rejection_and_evaluator_failure_are_isolated(tmp_path):
    saturated = _observer(tmp_path, service_factory=_RejectingService)
    saturated.start("PAPER")
    saturated.observe_decision(**_decision())
    assert saturated.metrics().rejected == 1
    assert saturated.metrics().accepted == 0
    assert saturated.close()

    store = _MemoryStore()

    def broken_factory(store, **kwargs):
        return EntryOpportunityValueService(
            store, **kwargs,
            evaluator=lambda *_args, **_values: (_ for _ in ()).throw(
                RuntimeError("evaluation failed")
            ),
        )

    broken = _observer(
        tmp_path, service_factory=broken_factory,
        store_factory=lambda _path: store,
    )
    broken.start("PAPER")
    broken.observe_decision(**_decision())
    assert broken.close(timeout_seconds=2)
    assert broken.metrics().failed == 1
    assert store.items == []


def test_repeated_start_shutdown_does_not_leak_worker(tmp_path):
    store = _MemoryStore()
    observer = _observer(
        tmp_path, store_factory=lambda _path: store,
        service_factory=EntryOpportunityValueService,
    )
    before = sum(
        thread.name == "atlas-entry-value-research"
        for thread in enumerate_threads()
    )
    for _ in range(3):
        observer.start("PAPER")
        observer.observe_decision(**_decision(
            lifecycle=f"WARRIOR_MOMENTUM_V1|CHPT|{_}",
        ))
        assert observer.close(timeout_seconds=2)
        assert observer.metrics().stopped is True
    after = sum(
        thread.name == "atlas-entry-value-research"
        for thread in enumerate_threads()
    )
    assert after == before
