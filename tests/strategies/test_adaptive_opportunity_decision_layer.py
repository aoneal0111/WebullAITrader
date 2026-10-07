from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from app.strategies.warrior_momentum.opportunity_engine import (
    AdaptiveOpportunityResult,
    OpportunityReason,
    WarriorOpportunityAssessment,
    WarriorOpportunityEngine,
    WarriorOpportunityState,
)


NOW = datetime(2026, 10, 7, 22, 45, tzinfo=timezone.utc)
D = Decimal


def assessment(**overrides):
    base = WarriorOpportunityAssessment(
        symbol="DKI",
        setup_family="RECLAIM_CONTINUATION",
        generation_id="g1",
        lifecycle_id="WARRIOR|DKI|g1",
        decision_timestamp=NOW,
        scanner_timestamp=NOW,
        quote_timestamp=NOW,
        last_timestamp=NOW,
        structural_trigger=D("2.15"),
        executable_entry=D("2.16"),
        structural_stop=D("2.08"),
        risk_per_share=D("0.08"),
        target_levels=(D("2.28"),),
        remaining_first_target_r=D("1.5"),
        remaining_final_target_r=D("1.5"),
        spread_percent=D("0.46"),
        execution_cost=D("0.01"),
        execution_quality="EXCELLENT",
        dollar_liquidity=D("100000000"),
        relative_volume=D("27"),
        volatility_context=D("0.12"),
        catalyst_context="TRUE",
        setup_quality=D("85"),
        momentum_priority=D("85"),
        displacement_percent=D("5"),
        adaptive_result=AdaptiveOpportunityResult.EXECUTABLE,
        reason_codes=(),
    )
    return replace(base, **overrides)


def test_soft_displacement_structural_rejection_is_normalized_to_wait():
    engine = WarriorOpportunityEngine()
    value = assessment(
        adaptive_result=AdaptiveOpportunityResult.STRUCTURALLY_REJECTED,
        reason_codes=(OpportunityReason.EXCESSIVE_CURRENT_STRUCTURE_DISPLACEMENT,),
    )

    item = engine.apply_assessment(value)

    assert item.state is WarriorOpportunityState.WAITING_EXECUTION
    assert item.reason is OpportunityReason.EXCESSIVE_CURRENT_STRUCTURE_DISPLACEMENT
    assert item.terminal_reason is None


def test_multiple_soft_entry_constraints_remain_one_live_opportunity():
    engine = WarriorOpportunityEngine()
    value = assessment(
        adaptive_result=AdaptiveOpportunityResult.STRUCTURALLY_REJECTED,
        reason_codes=(
            OpportunityReason.EXECUTABLE_TRIGGER_NOT_CONFIRMED,
            OpportunityReason.EXECUTION_QUALITY_WAIT,
            OpportunityReason.INSUFFICIENT_CURRENT_REWARD,
        ),
    )

    item = engine.apply_assessment(value)

    assert item.state is WarriorOpportunityState.WAITING_EXECUTION
    assert item.revision == 1
    assert engine.get("DKI", "g1") is item


def test_real_structural_invalidation_remains_terminal():
    engine = WarriorOpportunityEngine()
    value = assessment(
        adaptive_result=AdaptiveOpportunityResult.STRUCTURALLY_REJECTED,
        reason_codes=(OpportunityReason.SETUP_INVALIDATED,),
    )

    item = engine.apply_assessment(value)

    assert item.state is WarriorOpportunityState.INVALIDATED
    assert item.terminal_reason is OpportunityReason.SETUP_INVALIDATED


def test_hard_risk_failure_remains_fail_closed():
    engine = WarriorOpportunityEngine()
    value = assessment(
        adaptive_result=AdaptiveOpportunityResult.WAIT,
        hard_safety_reason=OpportunityReason.RISK_NOT_AUTHORIZED,
        reason_codes=(OpportunityReason.RISK_NOT_AUTHORIZED,),
    )

    item = engine.apply_assessment(value)

    assert item.state is WarriorOpportunityState.REJECTED_HARD_SAFETY
    assert item.terminal_reason is OpportunityReason.RISK_NOT_AUTHORIZED


def test_recoverable_provider_failure_waits_for_next_quote():
    engine = WarriorOpportunityEngine()
    value = assessment(
        adaptive_result=AdaptiveOpportunityResult.WAIT,
        hard_safety_reason=OpportunityReason.PROVIDER_DATA_STALE,
        reason_codes=(OpportunityReason.PROVIDER_DATA_STALE,),
    )

    item = engine.apply_assessment(value)

    assert item.state is WarriorOpportunityState.WAITING_EXECUTION
    assert item.terminal_reason is None
