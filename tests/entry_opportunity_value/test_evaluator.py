from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.entry_opportunity_value import (
    ComponentAvailability,
    EntryOpportunityValueInput,
    EvaluationPolicy,
    OpportunityTrend,
    ShadowAction,
    evaluate_entry_opportunity,
)


D = Decimal
CUTOFF = datetime(2026, 9, 3, 20, 9, 1, 281000, tzinfo=UTC)


def entry_context(**changes) -> EntryOpportunityValueInput:
    values = dict(
        symbol="TEST",
        decision_cutoff=CUTOFF,
        environment="PAPER",
        session="AFTER_HOURS",
        strategy="WARRIOR_MOMENTUM_V1",
        setup="HIGH_OF_DAY_BREAKOUT",
        lifecycle_id="WARRIOR_MOMENTUM_V1|TEST|episode",
        opportunity_id="opportunity-1",
        entry_plan_at=CUTOFF - timedelta(seconds=10),
        entry_ready_at=CUTOFF - timedelta(seconds=2),
        planned_entry_price=D("10.00"),
        structural_stop=D("9.90"),
        planned_quantity=100,
        setup_detected_at=CUTOFF - timedelta(seconds=20),
        bid=D("9.99"),
        ask=D("10.00"),
        last=D("10.00"),
        quote_timestamp=CUTOFF - timedelta(milliseconds=100),
        quote_received_at=CUTOFF - timedelta(milliseconds=50),
        scanner_rank=1,
        scanner_score=D("65"),
        percentage_change=D("12"),
        relative_volume=D("8"),
        detector_memberships=("HIGHER_LOW_CONTINUATION",),
        technical_state="QUALIFIED",
        entry_ready_state="ENTRY_READY",
        valid_until=CUTOFF + timedelta(seconds=50),
        day_boundary=CUTOFF + timedelta(hours=3),
    )
    values.update(changes)
    return EntryOpportunityValueInput(**values)


@pytest.mark.parametrize(
    ("ask", "expected_drift"),
    [
        (D("10.00"), D("0")),
        (D("9.98"), D("-0.02")),
        (D("10.02"), D("0.02")),
        (D("10.50"), D("0.50")),
    ],
)
def test_zero_favorable_small_and_large_adverse_drift(ask, expected_drift):
    observation = evaluate_entry_opportunity(entry_context(ask=ask, bid=ask - D("0.01")))
    assert observation.features.absolute_price_drift == expected_drift
    assert observation.features.price_drift_in_r == expected_drift / D("0.10")
    assert observation.execution_authorized is False
    assert observation.research_only is True


def test_invalid_zero_risk_is_honestly_unavailable():
    observation = evaluate_entry_opportunity(
        entry_context(planned_entry_price=D("9.90"), structural_stop=D("9.90"))
    )
    assert observation.features.original_risk_per_share is None
    assert observation.shadow_action is ShadowAction.INSUFFICIENT_EVIDENCE
    assert component(observation, "RISK_GEOMETRY").availability is ComponentAvailability.UNAVAILABLE


def test_stale_and_missing_quotes_are_not_scored_as_economic_evidence():
    stale = evaluate_entry_opportunity(
        entry_context(quote_timestamp=CUTOFF - timedelta(seconds=6))
    )
    missing = evaluate_entry_opportunity(
        entry_context(bid=None, ask=None, quote_timestamp=None, quote_received_at=None)
    )
    assert stale.shadow_action is ShadowAction.INSUFFICIENT_EVIDENCE
    assert stale.features.quote_age_ms == D("6000.0")
    assert component(stale, "DATA_QUALITY").value == 0
    assert missing.features.absolute_price_drift is None
    assert component(missing, "DATA_QUALITY").availability is ComponentAvailability.UNAVAILABLE


def test_spread_widening_increases_l1_friction_without_estimating_round_trip():
    narrow = evaluate_entry_opportunity(entry_context(bid=D("9.99"), ask=D("10.00")))
    wide = evaluate_entry_opportunity(entry_context(bid=D("9.90"), ask=D("10.00")))
    assert wide.features.spread_cost_in_r > narrow.features.spread_cost_in_r
    assert wide.features.estimated_round_trip_top_of_book_cost is None


@pytest.mark.parametrize("state", ["ENTRY_STALE", "DAY_EXPIRED"])
def test_expired_entry_is_shadow_abandon_stale(state):
    observation = evaluate_entry_opportunity(entry_context(order_terminal_state=state))
    assert observation.shadow_action is ShadowAction.ABANDON_STALE


def test_nearing_expiry_records_remaining_horizon_without_extending_it():
    valid_until = CUTOFF + timedelta(milliseconds=250)
    observation = evaluate_entry_opportunity(entry_context(valid_until=valid_until))
    assert observation.features.remaining_validity_ms == 250
    assert observation.features.setup_age_ms == 20000
    assert observation.context.valid_until == valid_until


def test_structural_stop_invalidation_is_distinct():
    observation = evaluate_entry_opportunity(
        entry_context(bid=D("9.88"), ask=D("9.90"), last=D("9.89"))
    )
    assert observation.shadow_action is ShadowAction.ABANDON_RISK_GEOMETRY
    assert "structural stop" in observation.action_reason


def test_memberships_are_immutable_normalized_context_only():
    observation = evaluate_entry_opportunity(entry_context(
        detector_memberships=("deep_pullback_reclaim", "HIGHER_LOW_CONTINUATION", "deep_pullback_reclaim")
    ))
    assert observation.context.detector_memberships == (
        "DEEP_PULLBACK_RECLAIM",
        "HIGHER_LOW_CONTINUATION",
    )


def test_current_risk_inflation_and_non_marketable_limit_are_explicit():
    observation = evaluate_entry_opportunity(entry_context(bid=D("10.19"), ask=D("10.20")))
    assert observation.features.original_limit_marketable is False
    assert observation.features.current_entry_risk_per_share == D("0.30")
    assert observation.features.current_entry_risk_multiple_vs_original == D("3")
    assert observation.features.would_original_quantity_violate_risk_at_ask is True
    assert observation.features.current_risk_budget_quantity == 33


def test_confidence_can_rise_while_entry_value_falls():
    first = evaluate_entry_opportunity(entry_context(technical_confidence=D("60")))
    later = evaluate_entry_opportunity(
        replace(
            entry_context(technical_confidence=D("70"), bid=D("10.19"), ask=D("10.20")),
            decision_cutoff=CUTOFF + timedelta(seconds=1),
            quote_timestamp=CUTOFF + timedelta(milliseconds=900),
            quote_received_at=CUTOFF + timedelta(milliseconds=950),
        ),
        previous=first,
        evaluated_at=CUTOFF + timedelta(seconds=1),
    )
    assert later.opportunity_trend is OpportunityTrend.CONFIDENCE_UP_ENTRY_VALUE_DOWN


def test_no_probability_or_remaining_move_is_fabricated():
    observation = evaluate_entry_opportunity(entry_context())
    assert component(observation, "CONTINUATION_CONFIDENCE").value is None
    assert component(observation, "EXPECTED_REMAINING_MOVE").value is None
    assert observation.shadow_action is ShadowAction.INSUFFICIENT_EVIDENCE


def test_estimates_require_explicit_point_in_time_provenance():
    with pytest.raises(ValueError, match="provenance"):
        entry_context(continuation_probability=D("0.6"))


def test_complete_evidence_can_emit_research_reprice_candidate_only():
    context = entry_context(
        bid=D("10.09"),
        ask=D("10.10"),
        continuation_probability=D("0.8"),
        continuation_probability_basis="gated historical analog cohort v1",
        continuation_probability_observed_at=CUTOFF,
        expected_remaining_move=D("0.50"),
        expected_remaining_move_basis="decision-time completed-bar range",
        expected_remaining_move_observed_at=CUTOFF,
        expected_downside=D("0.10"),
        expected_downside_basis="structural-stop distance",
        expected_downside_observed_at=CUTOFF,
    )
    observation = evaluate_entry_opportunity(context)
    assert observation.shadow_action is ShadowAction.REPRICE_CANDIDATE
    candidate = observation.reprice_candidates[0]
    assert candidate.execution_authorized is False
    assert candidate.research_only is True
    assert candidate.entry_price == context.ask
    assert component(observation, "OPPORTUNITY_VALUE").availability is ComponentAvailability.AVAILABLE


def test_decision_features_reject_future_timestamp_contamination():
    with pytest.raises(ValueError, match="cannot exceed decision cutoff"):
        entry_context(quote_timestamp=CUTOFF + timedelta(microseconds=1))
    with pytest.raises(ValueError, match="cannot exceed decision cutoff"):
        entry_context(entry_ready_at=CUTOFF + timedelta(microseconds=1))
    with pytest.raises(ValueError, match="evidence cannot exceed decision cutoff"):
        entry_context(
            expected_remaining_move=D("0.50"),
            expected_remaining_move_basis="future contamination",
            expected_remaining_move_observed_at=CUTOFF + timedelta(microseconds=1),
        )


def test_chpt_fixture_calculates_actual_geometry_without_special_case():
    observation = evaluate_entry_opportunity(entry_context(
        symbol="CHPT",
        lifecycle_id="WARRIOR_MOMENTUM_V1|CHPT|2026-09-03|HIGH_OF_DAY_BREAKOUT",
        planned_entry_price=D("9.104550"),
        structural_stop=D("9.05"),
        planned_quantity=1833,
        bid=D("9.20"),
        ask=D("9.25"),
        last=D("9.22"),
    ))
    assert observation.features.original_risk_per_share == D("0.054550")
    assert observation.features.absolute_price_drift == D("0.145450")
    assert observation.features.price_drift_in_r == D("0.145450") / D("0.054550")
    assert observation.features.current_entry_risk_per_share == D("0.20")
    assert observation.features.current_entry_risk_multiple_vs_original == D("0.20") / D("0.054550")
    assert observation.features.original_limit_marketable is False
    assert observation.shadow_action is ShadowAction.INSUFFICIENT_EVIDENCE
    assert observation.execution_authorized is False


def test_policy_is_research_only_and_does_not_change_context():
    policy = EvaluationPolicy(material_drift_in_r=D("5"))
    context = entry_context()
    observation = evaluate_entry_opportunity(context, policy=policy)
    assert observation.context is context
    assert observation.execution_authorized is False


def component(observation, name):
    return next(item for item in observation.components if item.name == name)
