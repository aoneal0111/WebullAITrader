from __future__ import annotations

import ast
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier, Event, Thread
from time import perf_counter
from types import SimpleNamespace

import pytest

from app.adaptive_entry_research import (
    AdaptiveEntryResearchWorker,
    AdaptiveWorkingEntryObserver,
    BoundedOutcomeTracker,
    JsonLinesResearchStore,
    MaterialChangeReason,
    ShadowRecommendation,
    WorkingEntrySnapshot,
    detect_material_change,
    evaluate_reassessment,
    label_outcome,
    resize_to_original_risk_budget,
)
from app.strategies.warrior_momentum.desktop_sidecar import (
    CompositeMarketEventObserver,
)


D = Decimal
T0 = datetime(2026, 9, 4, 14, 43, 57, 714730, tzinfo=UTC)


def snapshot(**changes) -> WorkingEntrySnapshot:
    values = dict(
        schema_version="1", market_event_at=T0 + timedelta(seconds=3),
        observed_at=T0 + timedelta(seconds=3),
        decision_cutoff=T0 + timedelta(seconds=3), environment="PAPER",
        symbol="CDTG", strategy_id="WARRIOR_MOMENTUM_V1",
        strategy_version="WARRIOR_MOMENTUM_V1",
        strategy_lifecycle_id="WARRIOR_MOMENTUM_V1|CDTG|2026-09-04|FLAT_TOP_BREAKOUT|1.2006|1.160",
        setup_type="FLAT_TOP_BREAKOUT", setup_state="TRIGGERED",
        order_id="cdtg-entry", side="BUY", order_type="LIMIT",
        order_status="ACCEPTED", original_limit_price=D("1.2006"),
        original_quantity=2463, remaining_quantity=2463, filled_quantity=0,
        original_structural_stop=D("1.160"), original_risk_per_share=D("0.0406"),
        original_total_risk=D("99.9978"), order_submitted_at=T0,
        order_state_at=T0,
        entry_valid_until=T0 + timedelta(seconds=60), working_age_seconds=D("3"),
        remaining_validity_seconds=D("57"), bid=D("1.235"), ask=D("1.240"),
        last=D("1.240"), quote_timestamp=T0 + timedelta(seconds=3),
        last_timestamp=T0 + timedelta(seconds=3),
        warrior_evidence_at=T0 + timedelta(seconds=3),
        position_evidence_at=T0 + timedelta(seconds=3),
        quote_freshness_seconds=D("0"), spread=D("0.005"),
        spread_percent=D("0.403225806"), scanner_rank=1, scanner_score=D("85"),
        relative_volume=D("12"), percentage_change=D("30"), volume=D("1000000"),
        dollar_volume=D("1240000"), float_shares=D("5000000"),
        warrior_current_state="ENTRY_READY", current_reference_price=D("1.240"),
        current_structural_stop=D("1.180"), current_setup_quality=D("0.90"),
        current_technical_actionable=True, existing_position_quantity=0,
        unavailable_evidence=("momentum_velocity", "volume_acceleration"),
    )
    values.update(changes)
    return WorkingEntrySnapshot(**values)


def test_contract_rejects_live_and_future_data():
    with pytest.raises(ValueError, match="LIVE"):
        snapshot(environment="LIVE")
    with pytest.raises(ValueError, match="quote_timestamp"):
        snapshot(quote_timestamp=T0 + timedelta(seconds=4))
    with pytest.raises(ValueError, match="last_timestamp"):
        snapshot(last_timestamp=T0 + timedelta(seconds=4))
    with pytest.raises(ValueError, match="order_submitted_at"):
        snapshot(
            order_submitted_at=T0 + timedelta(seconds=4),
            order_state_at=T0 + timedelta(seconds=4),
        )
    with pytest.raises(ValueError, match="warrior_evidence_at"):
        snapshot(warrior_evidence_at=T0 + timedelta(seconds=4))
    with pytest.raises(ValueError, match="position_evidence_at"):
        snapshot(position_evidence_at=T0 + timedelta(seconds=4))


@pytest.mark.parametrize(("changes", "expected"), [
    ({"ask": D("1.204"), "last": D("1.204"), "current_reference_price": D("1.204"), "current_structural_stop": D("1.1634")}, ShadowRecommendation.KEEP_ORIGINAL_LIMIT),
    ({"ask": D("1.225"), "last": D("1.225"), "current_reference_price": D("1.225"), "current_structural_stop": D("1.175")}, ShadowRecommendation.WAIT_FOR_RETRACE),
    ({}, ShadowRecommendation.REPRICE_AND_RESIZE_CANDIDATE),
    ({"ask": D("1.330"), "last": D("1.330")}, ShadowRecommendation.ABANDON_PRICE_DRIFT),
    ({"current_reference_price": D("1.240"), "current_structural_stop": D("1.240")}, ShadowRecommendation.ABANDON_RISK_GEOMETRY),
    ({"setup_state": "INVALIDATED", "current_technical_actionable": False}, ShadowRecommendation.ABANDON_SETUP_INVALIDATED),
])
def test_conservative_recommendation_semantics(changes, expected):
    assert evaluate_reassessment(snapshot(**changes), (MaterialChangeReason.PRICE_DISPLACEMENT,)).recommendation is expected


def test_reprice_is_hard_gated_and_resized_from_original_budget():
    result = evaluate_reassessment(snapshot(), (MaterialChangeReason.PRICE_DISPLACEMENT,))
    assert result.recommendation is ShadowRecommendation.REPRICE_AND_RESIZE_CANDIDATE
    assert result.fresh_hypothetical.quantity == 1666
    assert result.fresh_hypothetical.quantity != 2463
    assert result.fresh_hypothetical.total_risk == D("99.96")
    assert result.fresh_quantity_delta == -797
    assert result.fresh_risk_per_share_delta == D("0.0194")
    assert result.fresh_total_risk_delta == D("-0.0378")
    assert result.original_limit_to_ask == D("-0.0394")
    assert result.fresh_entry_to_ask == D("0.000")
    assert result.research_only and not result.execution_authorized and not result.production_promoted
    no_structure = evaluate_reassessment(
        snapshot(current_reference_price=None, current_structural_stop=None),
        (MaterialChangeReason.PRICE_DISPLACEMENT,),
    )
    assert no_structure.recommendation is ShadowRecommendation.INSUFFICIENT_EVIDENCE


def test_partial_fill_consumes_budget_and_caps_remaining_quantity():
    plan = resize_to_original_risk_budget(
        snapshot(remaining_quantity=1463, filled_quantity=1000, existing_position_quantity=1000),
        D("1.240"), D("1.180"),
    )
    assert plan.quantity == 989
    assert plan.quantity <= 1463
    assert plan.total_risk == D("59.34")


def test_material_detector_is_event_scale_and_nearing_expiry_is_edge_triggered():
    original = snapshot(ask=D("1.201"), last=D("1.201"), bid=D("1.200"),
                        current_reference_price=D("1.2006"), current_structural_stop=D("1.160"))
    changed = snapshot()
    reasons = detect_material_change(original, changed)
    assert MaterialChangeReason.PRICE_DISPLACEMENT in reasons
    assert MaterialChangeReason.QUOTE_DISPLACEMENT in reasons
    assert detect_material_change(changed, changed) == ()
    near = replace(changed, remaining_validity_seconds=D("9"))
    assert MaterialChangeReason.ORDER_NEARING_EXPIRY in detect_material_change(changed, near)
    assert MaterialChangeReason.ORDER_NEARING_EXPIRY not in detect_material_change(near, near)


def test_stale_semantics_remain_a_shadow_classification():
    stale = snapshot(decision_cutoff=T0 + timedelta(seconds=60), observed_at=T0 + timedelta(seconds=60),
                     remaining_validity_seconds=D("0"), quote_timestamp=T0 + timedelta(seconds=60),
                     terminal_reason="ENTRY_STALE", order_status="EXPIRED")
    result = evaluate_reassessment(stale, (MaterialChangeReason.ORDER_TERMINATED,))
    assert result.recommendation is ShadowRecommendation.ABANDON_STALE
    assert result.execution_authorized is False


def test_outcomes_are_later_separate_labels():
    result = evaluate_reassessment(snapshot(), (MaterialChangeReason.PRICE_DISPLACEMENT,))
    label = label_outcome(result, observed_at=result.decision_cutoff + timedelta(seconds=15),
                          future_price=D("1.30"), high=D("1.31"), low=D("1.19"))
    assert label.horizon_seconds == 15 and label.labels_only
    assert label.original_limit_hypothetically_fillable is True
    assert label.research_only and not label.execution_authorized and not label.production_promoted
    with pytest.raises(ValueError):
        label_outcome(result, observed_at=result.decision_cutoff, future_price=D("1.30"))


def test_outcome_tracking_is_bounded_and_uses_fixed_horizons():
    tracker = BoundedOutcomeTracker(maximum_recommendations=2, maximum_points_per_recommendation=3)
    results = []
    for index in range(3):
        result = evaluate_reassessment(
            snapshot(order_id=f"order-{index}", strategy_lifecycle_id=f"lifecycle-{index}"),
            (MaterialChangeReason.PRICE_DISPLACEMENT,),
        )
        tracker.track(result)
        results.append(result)
    assert tracker.retained_recommendations == 2
    labels = ()
    for seconds in (1, 2, 5, 15):
        labels = tracker.observe(
            symbol="CDTG", observed_at=results[-1].decision_cutoff + timedelta(seconds=seconds),
            price=D("1.25"), high=D("1.26"), low=D("1.19"),
        )
    assert {item.horizon_seconds for item in labels} == {15}
    assert tracker.maximum_retained_points == 3


class MemoryStore:
    def __init__(self, fail=False):
        self.items, self.fail, self.closed = [], fail, False
    def append(self, value):
        if self.fail:
            raise OSError("research store unavailable")
        self.items.append(value)
    def close(self):
        self.closed = True


class BlockingStore(MemoryStore):
    def __init__(self):
        super().__init__()
        self.entered, self.release = Event(), Event()
    def append(self, value):
        self.entered.set()
        self.release.wait(2)
        super().append(value)


def test_worker_bounds_shutdown_and_persistence_failure_isolation():
    store = MemoryStore(fail=True)
    worker = AdaptiveEntryResearchWorker(store, capacity=2, state_limit=1)
    assert worker.observe(snapshot(), (MaterialChangeReason.PRICE_DISPLACEMENT,))
    assert worker.close(timeout_seconds=2)
    assert worker.metrics().failed == 1 and store.closed
    assert worker.metrics().queue_high_water <= 2


def test_throwing_evaluator_is_isolated_in_worker():
    def fail_evaluation(*_args):
        raise RuntimeError("evaluation unavailable")

    store = MemoryStore()
    worker = AdaptiveEntryResearchWorker(store, evaluator=fail_evaluation)
    assert worker.observe(snapshot(), (MaterialChangeReason.PRICE_DISPLACEMENT,))
    assert worker.close(timeout_seconds=2)
    assert worker.metrics().failed == 1
    assert store.items == [] and store.closed


def test_producer_admission_drops_instead_of_waiting_for_lock():
    worker = AdaptiveEntryResearchWorker(MemoryStore())
    assert worker._lock.acquire(blocking=False)
    try:
        started = perf_counter()
        accepted = worker.observe(
            snapshot(), (MaterialChangeReason.PRICE_DISPLACEMENT,),
        )
        elapsed = perf_counter() - started
    finally:
        worker._lock.release()
    assert not accepted and elapsed < 0.05
    assert worker.metrics().admission_contention_drops == 1
    assert worker.close(timeout_seconds=2)


def test_worker_shutdown_drains_a_bounded_backlog():
    store = BlockingStore()
    worker = AdaptiveEntryResearchWorker(store, capacity=1, state_limit=1)
    assert worker.observe(snapshot(), (MaterialChangeReason.PRICE_DISPLACEMENT,))
    assert store.entered.wait(1)
    second = snapshot(order_id="second", strategy_lifecycle_id="second")
    assert worker.observe(second, (MaterialChangeReason.PRICE_DISPLACEMENT,))
    assert not worker.observe(snapshot(order_id="third", strategy_lifecycle_id="third"), (MaterialChangeReason.PRICE_DISPLACEMENT,))
    store.release.set()
    assert worker.close(timeout_seconds=2)
    metrics = worker.metrics()
    assert metrics.completed == 2 and metrics.rejected == 1
    assert metrics.queue_high_water == 1 and metrics.retained_orders == 1


def test_worker_persists_future_outcomes_separately_after_recommendation():
    recommendations, outcomes = MemoryStore(), MemoryStore()
    worker = AdaptiveEntryResearchWorker(
        recommendations, outcome_store=outcomes, capacity=4,
    )
    current = snapshot()
    assert worker.observe(current, (MaterialChangeReason.PRICE_DISPLACEMENT,))
    at_horizon = current.decision_cutoff + timedelta(seconds=5)
    assert worker.observe_market(symbol="CDTG", observed_at=at_horizon, price=D("1.27"))
    assert not worker.observe_market(symbol="CDTG", observed_at=at_horizon, price=D("1.27"))
    assert worker.close(timeout_seconds=2)
    assert len(recommendations.items) == 1
    assert len(outcomes.items) == 1 and outcomes.items[0].horizon_seconds == 5
    assert outcomes.items[0].labels_only and not outcomes.items[0].execution_authorized
    metrics = worker.metrics()
    assert metrics.outcome_points_accepted == 1
    assert metrics.outcome_labels_completed == 1
    assert metrics.outcome_points_suppressed == 1
    assert recommendations.closed and outcomes.closed


def test_jsonl_persistence_has_explicit_authority_flags(tmp_path):
    path = tmp_path / "recommendations.jsonl"
    store = JsonLinesResearchStore(path)
    store.append(evaluate_reassessment(snapshot(), (MaterialChangeReason.PRICE_DISPLACEMENT,)))
    store.close()
    text = path.read_text(encoding="utf-8")
    assert '"research_only":true' in text
    assert '"execution_authorized":false' in text
    assert '"production_promoted":false' in text


def order(
    *,
    status="ACCEPTED",
    filled=0,
    side="BUY",
    order_type="LIMIT",
    execution_reason="ENTRY",
    created_at=T0,
    updated_at=None,
    order_id="cdtg-entry",
):
    request = SimpleNamespace(
        symbol="CDTG", side=SimpleNamespace(value=side), order_type=SimpleNamespace(value=order_type),
        quantity=D("2463"), limit_price=D("1.2006"), structural_stop_price=D("1.160"),
        execution_reason=execution_reason,
        strategy_lifecycle_id="WARRIOR_MOMENTUM_V1|CDTG|2026-09-04|FLAT_TOP_BREAKOUT|1.2006|1.160",
        entry_valid_until=T0 + timedelta(seconds=60),
    )
    return SimpleNamespace(order_id=order_id, request=request, status=SimpleNamespace(value=status),
                           created_at=created_at, updated_at=updated_at or created_at,
                           filled_quantity=D(filled), remaining_quantity=D(2463-filled))


def quote(at, bid="1.235", ask="1.240"):
    return SimpleNamespace(symbol="CDTG", timestamp=at, event_type=SimpleNamespace(value="QUOTE"),
                           payload=SimpleNamespace(bid=D(bid), ask=D(ask)))


class InlineWorker:
    def __init__(self, *_args, **_kwargs): self.items=[]
    def observe(self, snap, reasons): self.items.append((snap, reasons)); return True
    def close(self, **_kwargs): return True
    def metrics(self): return None


def test_concurrent_identical_admission_is_one_episode(tmp_path):
    gate = Barrier(2)

    class CoordinatedObserver(AdaptiveWorkingEntryObserver):
        def _snapshot(self, order, event, market_event_at, *, previous=None):
            gate.wait(timeout=2)
            return super()._snapshot(order, event, market_event_at, previous=previous)

    working = order(order_id="cdtg-race")
    observer = CoordinatedObserver(
        enabled=True, environment="PAPER", path=tmp_path / "race.jsonl",
        order_source=lambda _symbol: (working,),
        position_source=lambda _symbol: D("0"),
        warrior_source=lambda _symbol, cutoff: warrior_context(cutoff),
        worker_factory=InlineWorker, store_factory=lambda _path: MemoryStore(),
        clock=lambda: T0 + timedelta(seconds=3),
    )
    observer.start()
    event = quote(T0 + timedelta(seconds=3), bid="1.235", ask="1.240")
    threads = [Thread(target=observer, args=(event,)) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=3)
    assert all(not thread.is_alive() for thread in threads)
    assert len(observer._worker.items) == 1
    assert observer.metrics().concurrent_duplicate_suppressions >= 1
    observer.stop()


def test_worker_duplicate_admission_persists_one_row_and_one_outcome_identity():
    store = MemoryStore()
    tracker = BoundedOutcomeTracker()
    worker = AdaptiveEntryResearchWorker(store, capacity=4, outcome_tracker=tracker)
    reasons = (MaterialChangeReason.PRICE_DISPLACEMENT,)
    assert worker.observe(snapshot(), reasons)
    assert not worker.observe(snapshot(), reasons)
    assert worker.close(timeout_seconds=2)
    assert len(store.items) == 1
    assert worker.metrics().semantic_repeats_suppressed == 1
    assert tracker.retained_recommendations == 1


def test_semantic_episode_suppresses_spread_noise_but_emits_transitions(tmp_path):
    now = [T0 + timedelta(seconds=3)]
    context = {"setup_state": "TRIGGERED", "action": True}

    def warrior(_symbol, cutoff):
        return {"observed_at": cutoff, "setup_state": context["setup_state"],
                "current_reference_price": D("1.225"),
                "current_structural_stop": D("1.175"),
                "current_setup_quality": D("0.90"),
                "current_technical_actionable": context["action"]}

    working = order(order_id="sgrx-episode")
    observer = AdaptiveWorkingEntryObserver(
        enabled=True, environment="PAPER", path=tmp_path / "episodes.jsonl",
        order_source=lambda _symbol: (working,), position_source=lambda _symbol: D("0"),
        warrior_source=warrior, worker_factory=InlineWorker,
        store_factory=lambda _path: MemoryStore(), clock=lambda: now[0],
    )
    observer.start()
    for index in range(20):
        bid = D("1.224") - D(index) / D("100000")
        observer(quote(T0 + timedelta(seconds=3, milliseconds=index), bid=str(bid), ask="1.225"))
    assert len(observer._worker.items) == 1
    assert observer.metrics().semantic_repeats_suppressed >= 10
    # A meaningful displacement bucket transition is immediate.
    observer(quote(T0 + timedelta(seconds=4), bid="1.239", ask="1.240"))
    assert len(observer._worker.items) == 2
    # Near-expiry and setup/actionability transitions are not hidden by a cooldown.
    now[0] = T0 + timedelta(seconds=55)
    observer(quote(T0 + timedelta(seconds=55), bid="1.239", ask="1.240"))
    assert len(observer._worker.items) == 3
    context["setup_state"], context["action"] = "INVALIDATED", False
    observer(quote(T0 + timedelta(seconds=56), bid="1.239", ask="1.240"))
    assert len(observer._worker.items) == 4
    observer.stop()


def warrior_context(at):
    return {
        "observed_at": at,
        "setup_state": "TRIGGERED",
        "current_reference_price": D("1.240"),
        "current_structural_stop": D("1.180"),
        "current_setup_quality": D("0.90"),
        "current_technical_actionable": True,
    }


def test_runtime_cutoff_is_causal_when_event_precedes_order_creation(tmp_path):
    market_event_at = T0
    submitted_at = T0 + timedelta(seconds=1)
    adopted_at = T0 + timedelta(seconds=2)
    working = order(created_at=submitted_at, updated_at=submitted_at)
    observer = AdaptiveWorkingEntryObserver(
        enabled=True,
        environment="PAPER",
        path=tmp_path / "causal.jsonl",
        order_source=lambda _symbol: (working,),
        position_source=lambda _symbol: (D("0"), adopted_at),
        warrior_source=lambda _symbol, _cutoff: warrior_context(market_event_at),
        worker_factory=InlineWorker,
        store_factory=lambda _path: MemoryStore(),
        clock=lambda: adopted_at,
    )
    observer.start()
    observer(quote(market_event_at))
    captured = observer._worker.items[0][0]
    assert captured.market_event_at == market_event_at
    assert captured.order_submitted_at == submitted_at
    assert captured.order_state_at == submitted_at
    assert captured.observed_at == adopted_at
    assert captured.decision_cutoff == adopted_at
    assert captured.decision_cutoff >= captured.order_submitted_at
    assert captured.quote_timestamp <= captured.decision_cutoff
    assert captured.warrior_evidence_at <= captured.decision_cutoff
    assert captured.position_evidence_at <= captured.decision_cutoff
    observer.stop()


def test_runtime_rejects_future_warrior_evidence_instead_of_moving_cutoff(tmp_path):
    adopted_at = T0 + timedelta(seconds=3)
    observer = AdaptiveWorkingEntryObserver(
        enabled=True,
        environment="PAPER",
        path=tmp_path / "future.jsonl",
        order_source=lambda _symbol: (order(),),
        position_source=lambda _symbol: (D("0"), adopted_at),
        warrior_source=lambda _symbol, _cutoff: warrior_context(
            adopted_at + timedelta(seconds=1),
        ),
        worker_factory=InlineWorker,
        store_factory=lambda _path: MemoryStore(),
        clock=lambda: adopted_at,
    )
    observer.start()
    observer(quote(adopted_at))
    assert observer._worker.items == []
    assert observer.metrics().failed == 1
    observer.stop()


def test_composite_orders_authority_first_and_isolates_adaptive_exception():
    sequence = []

    def primary(_event):
        sequence.append("PAPER_GATEWAY")

    def warrior(_event):
        sequence.append("WARRIOR_SUBMISSION")

    def adaptive(_event):
        sequence.append("ADAPTIVE_RESEARCH")
        raise RuntimeError("research failure")

    composite = CompositeMarketEventObserver(
        primary, warrior, adaptive_entry=adaptive,
    )
    composite(object())
    composite(object())
    assert sequence == [
        "PAPER_GATEWAY", "WARRIOR_SUBMISSION", "ADAPTIVE_RESEARCH",
        "PAPER_GATEWAY", "WARRIOR_SUBMISSION", "ADAPTIVE_RESEARCH",
    ]
    assert composite.adaptive_entry_failures == 2


def test_cdtg_event_reassessment_precedes_stale_and_never_mutates_order(tmp_path):
    working = order()
    calls = {"orders": 0}
    def orders(_symbol): calls["orders"] += 1; return (working,)
    observer = AdaptiveWorkingEntryObserver(
        enabled=True, environment="PAPER", path=tmp_path / "research.jsonl",
        order_source=orders, position_source=lambda _symbol: D("0"),
        warrior_source=lambda _symbol, _cutoff: warrior_context(T0 + timedelta(seconds=3)),
        worker_factory=InlineWorker, store_factory=lambda _path: MemoryStore(),
        clock=lambda: T0 + timedelta(seconds=3),
    )
    observer.start()
    before = repr(working)
    observer(quote(T0 + timedelta(seconds=3)))
    worker = observer._worker
    assert len(worker.items) == 1
    captured, reasons = worker.items[0]
    result = evaluate_reassessment(captured, reasons)
    assert captured.working_age_seconds == D("3.0")
    assert result.recommendation is ShadowRecommendation.REPRICE_AND_RESIZE_CANDIDATE
    assert result.fresh_hypothetical.quantity == 1666
    assert repr(working) == before and calls["orders"] == 1
    observer(quote(T0 + timedelta(seconds=3)))
    assert len(worker.items) == 1 and observer.metrics().suppressed == 1
    observer.stop()


def test_runtime_partial_fill_preserves_exposure_and_caps_reprice(tmp_path):
    partial = order(status="PARTIALLY_FILLED", filled=1000)
    observer = AdaptiveWorkingEntryObserver(
        enabled=True,
        environment="PAPER",
        path=tmp_path / "partial.jsonl",
        order_source=lambda _symbol: (partial,),
        position_source=lambda _symbol: D("1000"),
        warrior_source=lambda _symbol, _cutoff: warrior_context(
            T0 + timedelta(seconds=3),
        ),
        worker_factory=InlineWorker,
        store_factory=lambda _path: MemoryStore(),
        clock=lambda: T0 + timedelta(seconds=3),
    )
    observer.start()
    observer(quote(T0 + timedelta(seconds=3)))
    captured, reasons = observer._worker.items[0]
    result = evaluate_reassessment(captured, reasons)
    assert captured.remaining_quantity == 1463
    assert captured.filled_quantity == 1000
    assert captured.existing_position_quantity == 1000
    assert result.fresh_hypothetical.quantity == 989
    assert result.fresh_hypothetical.quantity <= captured.remaining_quantity
    observer.stop()


def test_terminal_sell_and_live_are_ineligible(tmp_path):
    terminal = order(status="EXPIRED")
    observer = AdaptiveWorkingEntryObserver(
        enabled=True, environment="PAPER", path=tmp_path / "x.jsonl",
        order_source=lambda _symbol: (terminal,), position_source=lambda _symbol: D("0"),
        worker_factory=InlineWorker, store_factory=lambda _path: MemoryStore(),
    )
    observer.start(); observer(quote(T0 + timedelta(seconds=3)))
    assert observer.metrics().eligible_orders == 0
    live = AdaptiveWorkingEntryObserver(
        enabled=True, environment="LIVE", path=tmp_path / "live.jsonl",
        order_source=lambda _symbol: (order(),), position_source=lambda _symbol: D("0"),
    )
    live.start(); live(quote(T0 + timedelta(seconds=3)))
    assert live.metrics().enabled is False and live.metrics().observed_events == 0


def test_sell_stop_target_and_non_entry_orders_are_ineligible(tmp_path):
    ineligible = (
        order(side="SELL", execution_reason="TARGET", order_id="sell"),
        order(order_type="STOP", execution_reason="PROTECTIVE_STOP", order_id="stop"),
        order(side="SELL", order_type="LIMIT", execution_reason="TARGET", order_id="target"),
        order(execution_reason="PROTECTIVE_REPLACED", order_id="not-entry"),
    )
    observer = AdaptiveWorkingEntryObserver(
        enabled=True,
        environment="PAPER",
        path=tmp_path / "ineligible.jsonl",
        order_source=lambda _symbol: ineligible,
        position_source=lambda _symbol: D("0"),
        worker_factory=InlineWorker,
        store_factory=lambda _path: MemoryStore(),
        clock=lambda: T0 + timedelta(seconds=3),
    )
    observer.start()
    observer(quote(T0 + timedelta(seconds=3)))
    assert observer.metrics().eligible_orders == 0
    assert observer._worker.items == []
    observer.stop()


def test_rapid_distinct_updates_drop_under_pressure_without_blocking():
    store = BlockingStore()
    worker = AdaptiveEntryResearchWorker(store, capacity=2, state_limit=4)
    assert worker.observe(snapshot(), (MaterialChangeReason.PRICE_DISPLACEMENT,))
    assert store.entered.wait(1)
    started = perf_counter()
    results = [
        worker.observe(
            snapshot(
                order_id=f"rapid-{index}",
                strategy_lifecycle_id=f"rapid-{index}",
                ask=D("1.240") + D(index) / D("10000"),
            ),
            (MaterialChangeReason.PRICE_DISPLACEMENT,),
        )
        for index in range(100)
    ]
    elapsed = perf_counter() - started
    assert elapsed < 0.1
    assert any(results) and not all(results)
    assert worker.metrics().rejected > 0
    store.release.set()
    assert worker.close(timeout_seconds=2)


def test_queue_pressure_cannot_stop_authoritative_composite_processing():
    store = BlockingStore()
    worker = AdaptiveEntryResearchWorker(store, capacity=2, state_limit=4)
    assert worker.observe(snapshot(), (MaterialChangeReason.PRICE_DISPLACEMENT,))
    assert store.entered.wait(1)
    counts = {"paper": 0, "warrior": 0, "adaptive": 0}

    def paper(_event):
        counts["paper"] += 1

    def warrior(_event):
        counts["warrior"] += 1

    def adaptive(_event):
        index = counts["adaptive"]
        counts["adaptive"] += 1
        worker.observe(
            snapshot(
                order_id=f"pressure-{index}",
                strategy_lifecycle_id=f"pressure-{index}",
            ),
            (MaterialChangeReason.PRICE_DISPLACEMENT,),
        )

    composite = CompositeMarketEventObserver(
        paper, warrior, adaptive_entry=adaptive,
    )
    started = perf_counter()
    for _ in range(100):
        composite(object())
    elapsed = perf_counter() - started
    assert counts == {"paper": 100, "warrior": 100, "adaptive": 100}
    assert elapsed < 0.1
    assert worker.metrics().rejected > 0
    store.release.set()
    assert worker.close(timeout_seconds=2)


def test_10000_identical_updates_are_suppressed_and_fast(tmp_path):
    working = order()
    observer = AdaptiveWorkingEntryObserver(
        enabled=True, environment="TEST", path=tmp_path / "x.jsonl",
        order_source=lambda _symbol: (working,), position_source=lambda _symbol: D("0"),
        warrior_source=lambda _symbol, _cutoff: {"observed_at": T0 + timedelta(seconds=3)},
        worker_factory=InlineWorker, store_factory=lambda _path: MemoryStore(), state_limit=4,
        clock=lambda: T0 + timedelta(seconds=3),
    )
    observer.start(); event = quote(T0 + timedelta(seconds=3))
    started = perf_counter()
    for _ in range(10_000): observer(event)
    elapsed = perf_counter() - started
    assert len(observer._worker.items) == 1
    assert observer.metrics().suppressed == 9_999
    assert observer.metrics().retained_order_state <= 4
    assert observer.metrics().retained_signatures <= 4
    assert elapsed < 3


def test_package_has_no_execution_authority_imports_or_calls():
    forbidden_modules = ("paper_gateway", "paper_trading", "order_placement", "order_cancellation", "live_execution", "broker_execution")
    forbidden_calls = {"place_order", "cancel_order", "replace_order", "submit_order", "modify_order"}
    for path in Path("app/adaptive_entry_research").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports, calls = [], []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import): imports.extend(item.name for item in node.names)
            elif isinstance(node, ast.ImportFrom): imports.append(node.module or "")
            elif isinstance(node, ast.Call):
                calls.append(node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else "")
        assert not any(fragment in module for fragment in forbidden_modules for module in imports)
        assert forbidden_calls.isdisjoint(calls)
