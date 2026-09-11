from datetime import timedelta
from decimal import Decimal

from app.opportunity_discovery import (
    AdapterRejection, DetectionState, MultiStrategyExecutionAdapter,
)
from tests.opportunity_discovery.conftest import clean_pullback, context


def _opportunity():
    from app.opportunity_discovery import MultiStrategyDiscoveryEngine
    return MultiStrategyDiscoveryEngine().observe(context(clean_pullback())).opportunities[0]


def _kwargs(opportunity):
    return {
        "strategy_scores": {item.strategy_id: Decimal("80") for item in opportunity.memberships},
        "observed_at": opportunity.decision_cutoff + timedelta(seconds=1),
        "freshness_max_age": timedelta(seconds=5),
        "freshness_authority": "DECISION_CUTOFF",
        "spread_percent": Decimal("0.2"),
        "dollar_volume": Decimal("2500000"),
    }


def test_research_only_is_preserved_but_explicit_micro_allowlist_adapts():
    opportunity = _opportunity()
    result = MultiStrategyExecutionAdapter().adapt(opportunity, **_kwargs(opportunity))
    assert result.candidate is not None
    assert result.candidate.selected_execution_strategy == "MICRO_PULLBACK"
    assert result.candidate.trigger_price > result.candidate.structural_stop
    assert "FIRST_PULLBACK" in result.candidate.strategy_memberships
    setup = result.candidate.as_warrior_setup()
    assert setup.setup_type.value == "MICRO_PULLBACK"
    assert setup.trigger == result.candidate.trigger_price


def test_non_allowlisted_strategy_fails_closed():
    opportunity = _opportunity()
    result = MultiStrategyExecutionAdapter(execution_allowlist={"FIRST_PULLBACK"}).adapt(
        opportunity, **_kwargs(opportunity)
    )
    assert result.rejection_reason is AdapterRejection.MISSING_INVALIDATION


def test_allowlisted_strategy_without_invalidation_capability_fails_closed():
    opportunity = _opportunity()
    result = MultiStrategyExecutionAdapter(
        execution_allowlist={"MICRO_PULLBACK"}, invalidation_capabilities=set()
    ).adapt(opportunity, **_kwargs(opportunity))
    assert result.rejection_reason is AdapterRejection.MISSING_INVALIDATION


def test_missing_geometry_and_invalid_risk_fail_closed():
    opportunity = _opportunity()
    from dataclasses import replace
    from app.opportunity_discovery import normalize_detections
    from app.opportunity_discovery.detectors import default_registry
    detections = tuple(default_registry().evaluate(context(clean_pullback())))
    missing_trigger = normalize_detections(tuple(
        replace(item, trigger_level=None) if item.strategy_id == "MICRO_PULLBACK" else item
        for item in detections
    ))[0]
    result = MultiStrategyExecutionAdapter().adapt(missing_trigger, **_kwargs(missing_trigger))
    assert result.rejection_reason is AdapterRejection.MISSING_TRIGGER

    invalid_membership = replace(
        next(item for item in opportunity.memberships if item.strategy_id == "MICRO_PULLBACK"),
        trigger_level=Decimal("10"), structural_stop=Decimal("10"),
    )
    invalid_risk = replace(opportunity, memberships=(invalid_membership,))
    result = MultiStrategyExecutionAdapter().adapt(invalid_risk, **_kwargs(invalid_risk))
    assert result.rejection_reason is AdapterRejection.NON_POSITIVE_RISK

    missing_stop = replace(opportunity, memberships=(replace(
        next(item for item in opportunity.memberships if item.strategy_id == "MICRO_PULLBACK"),
        structural_stop=None,
    ),))
    result = MultiStrategyExecutionAdapter().adapt(missing_stop, **_kwargs(missing_stop))
    assert result.rejection_reason is AdapterRejection.MISSING_STRUCTURAL_STOP


def test_stale_context_and_duplicate_opportunity_are_distinct():
    opportunity = _opportunity()
    adapter = MultiStrategyExecutionAdapter()
    stale = adapter.adapt(opportunity, **{**_kwargs(opportunity), "observed_at": opportunity.decision_cutoff + timedelta(seconds=6)})
    assert stale.rejection_reason is AdapterRejection.STALE_CONTEXT
    first = adapter.adapt(opportunity, **_kwargs(opportunity))
    second = adapter.adapt(opportunity, **_kwargs(opportunity))
    assert first.candidate is not None
    assert second.rejection_reason is AdapterRejection.DUPLICATE_OPPORTUNITY


def test_selection_is_deterministic_and_overlaps_are_suppressed():
    opportunity = _opportunity()
    scores = {item.strategy_id: Decimal("80") for item in opportunity.memberships}
    first = MultiStrategyExecutionAdapter().adapt(opportunity, **{**_kwargs(opportunity), "strategy_scores": scores})
    second = MultiStrategyExecutionAdapter().adapt(opportunity, **{**_kwargs(opportunity), "strategy_scores": scores})
    assert first.candidate is not None and second.candidate is not None
    assert first.candidate.selected_execution_strategy == second.candidate.selected_execution_strategy == "MICRO_PULLBACK"
    assert first.candidate.suppressed_duplicate_strategies


def test_diagnostics_are_bounded_and_failures_do_not_escape():
    class Sink:
        def __init__(self): self.records = []
        def record_strategy_selection(self, **values): self.records.append(values)

    opportunity = _opportunity()
    sink = Sink()
    adapter = MultiStrategyExecutionAdapter(maximum_identities=2, diagnostics=sink)
    adapter.adapt(opportunity, **_kwargs(opportunity))
    assert len(sink.records) == 1
    assert sink.records[0]["selected_execution_strategy"] == "MICRO_PULLBACK"
    class Broken:
        def record_strategy_selection(self, **_values): raise RuntimeError("diagnostic failure")
    result = MultiStrategyExecutionAdapter().adapt(opportunity, **{**_kwargs(opportunity), "diagnostics": Broken()})
    assert result.candidate is not None


def test_performance_diagnostics_receive_bounded_selection_record():
    from app.performance_diagnostics import PerformanceDiagnostics
    diagnostics = PerformanceDiagnostics()
    result = MultiStrategyExecutionAdapter(diagnostics=diagnostics).adapt(
        _opportunity(), **_kwargs(_opportunity())
    )
    assert result.candidate is not None
    metrics = diagnostics.strategy_selection_metrics()
    assert len(metrics["records"]) == 1
    assert metrics["bounds"]["records"] == 256


def test_identity_memory_is_bounded_and_new_anchor_can_be_seen():
    from dataclasses import replace
    adapter = MultiStrategyExecutionAdapter(maximum_identities=2)
    opportunity = _opportunity()
    assert adapter.adapt(opportunity, **_kwargs(opportunity)).candidate is not None
    for index in (1, 2, 3):
        later = replace(opportunity, structural_anchor=f"new-anchor-{index}", opportunity_id=f"new-{index}")
        assert adapter.adapt(later, **_kwargs(later)).candidate is not None
    assert len(adapter._seen_identities) <= 2
