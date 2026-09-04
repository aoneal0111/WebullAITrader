"""Pure, explainable evaluation of decision-time entry opportunity value."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR
from hashlib import sha256

from .models import (
    BoundedRepriceCandidate,
    ComponentAvailability,
    EntryOpportunityValueInput,
    EntryOpportunityValueObservation,
    OpportunityComponent,
    OpportunityTrend,
    PriceDriftFeatures,
    ShadowAction,
)


ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")
THOUSAND = Decimal("1000")


@dataclass(frozen=True, slots=True)
class EvaluationPolicy:
    """Versioned research thresholds; never consumed by execution."""

    # Mirrors the established Warrior forward-capture default without
    # importing or mutating strategy configuration.
    quote_stale_after_ms: Decimal = Decimal("5000")
    material_drift_in_r: Decimal = Decimal("1")
    material_risk_inflation: Decimal = Decimal("2")
    version: str = "ENTRY_VALUE_RESEARCH_V1"

    def __post_init__(self) -> None:
        if min(
            self.quote_stale_after_ms,
            self.material_drift_in_r,
            self.material_risk_inflation,
        ) <= 0:
            raise ValueError("research evaluation thresholds must be positive")


def evaluate_entry_opportunity(
    context: EntryOpportunityValueInput,
    *,
    evaluated_at: datetime | None = None,
    previous: EntryOpportunityValueObservation | None = None,
    policy: EvaluationPolicy = EvaluationPolicy(),
) -> EntryOpportunityValueObservation:
    """Build one immutable observation without issuing any command."""

    created_at = evaluated_at or context.decision_cutoff
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")
    if created_at < context.decision_cutoff:
        raise ValueError("evaluation cannot precede decision cutoff")

    risk = context.planned_entry_price - context.structural_stop
    valid_risk = risk > ZERO
    quote_complete = (
        context.bid is not None
        and context.ask is not None
        and context.quote_timestamp is not None
    )
    ask = context.ask if quote_complete else None
    spread = context.ask - context.bid if quote_complete else None
    quote_age_ms = (
        Decimal(str((context.decision_cutoff - context.quote_timestamp).total_seconds()))
        * THOUSAND
        if quote_complete
        else None
    )
    quote_fresh = (
        quote_age_ms is not None
        and ZERO <= quote_age_ms <= policy.quote_stale_after_ms
    )

    drift = ask - context.planned_entry_price if ask is not None else None
    current_risk = ask - context.structural_stop if ask is not None else None
    risk_multiple = (
        current_risk / risk
        if valid_risk and current_risk is not None and current_risk > ZERO
        else None
    )
    drift_in_r = drift / risk if valid_risk and drift is not None else None
    spread_in_r = spread / risk if valid_risk and spread is not None else None
    original_budget = risk * context.planned_quantity if valid_risk else None
    resized_quantity = (
        int((original_budget / current_risk).to_integral_value(rounding=ROUND_FLOOR))
        if original_budget is not None and current_risk is not None and current_risk > ZERO
        else None
    )
    remaining_validity_ms = (
        int((context.valid_until - context.decision_cutoff).total_seconds() * 1000)
        if context.valid_until is not None
        else None
    )
    features = PriceDriftFeatures(
        original_risk_per_share=risk if valid_risk else None,
        absolute_price_drift=drift,
        price_drift_percent=(drift / context.planned_entry_price * HUNDRED if drift is not None else None),
        price_drift_in_r=drift_in_r,
        current_entry_risk_per_share=(current_risk if current_risk is not None and current_risk > ZERO else None),
        current_entry_risk_multiple_vs_original=risk_multiple,
        distance_from_trigger=drift,
        distance_from_stop=current_risk,
        spread_cost_per_share=spread,
        spread_percent=(spread / context.ask * HUNDRED if spread is not None and context.ask else None),
        spread_cost_in_r=spread_in_r,
        # L1 cannot establish the future exit side of a round trip.
        estimated_round_trip_top_of_book_cost=None,
        quote_age_ms=quote_age_ms,
        time_since_entry_plan_ms=int((context.decision_cutoff - context.entry_plan_at).total_seconds() * 1000),
        time_since_entry_ready_ms=int((context.decision_cutoff - context.entry_ready_at).total_seconds() * 1000),
        setup_age_ms=(
            int((context.decision_cutoff - context.setup_detected_at).total_seconds() * 1000)
            if context.setup_detected_at is not None
            else None
        ),
        remaining_validity_ms=remaining_validity_ms,
        original_limit_marketable=(context.planned_entry_price >= context.ask if quote_complete else None),
        distance_to_market=drift,
        current_risk_budget_quantity=resized_quantity,
        would_best_ask_entry_change_risk_budget=(resized_quantity != context.planned_quantity if resized_quantity is not None else None),
        would_original_quantity_violate_risk_at_ask=(
            current_risk * context.planned_quantity > original_budget
            if current_risk is not None and original_budget is not None
            else None
        ),
    )
    components = _components(context, features, quote_complete, quote_fresh)
    candidates = _reprice_candidates(context, features)
    action, action_reason = _action(context, features, quote_fresh, policy)
    observation = EntryOpportunityValueObservation(
        observation_id=_identity(context, policy),
        context=context,
        features=features,
        components=components,
        shadow_action=action,
        action_reason=action_reason,
        opportunity_trend=OpportunityTrend.UNAVAILABLE,
        reprice_candidates=candidates,
        created_at=created_at,
    )
    if previous is not None:
        observation = EntryOpportunityValueObservation(
            observation_id=observation.observation_id,
            context=observation.context,
            features=observation.features,
            components=observation.components,
            shadow_action=observation.shadow_action,
            action_reason=observation.action_reason,
            opportunity_trend=_trend(previous, observation),
            reprice_candidates=observation.reprice_candidates,
            created_at=observation.created_at,
        )
    return observation


def _components(context, features, quote_complete, quote_fresh):
    technical = context.technical_confidence
    if technical is None:
        technical = context.scanner_score
    technical_component = OpportunityComponent(
        "TECHNICAL_CONFIDENCE",
        technical,
        ComponentAvailability.UNCALIBRATED if technical is not None else ComponentAvailability.UNAVAILABLE,
        "point-in-time technical score; not a calibrated continuation probability"
        if technical is not None
        else "no point-in-time technical confidence was supplied",
    )
    continuation = OpportunityComponent(
        "CONTINUATION_CONFIDENCE",
        context.continuation_probability,
        ComponentAvailability.AVAILABLE if context.continuation_probability is not None else ComponentAvailability.UNAVAILABLE,
        context.continuation_probability_basis or "no calibrated continuation probability is available",
    )
    remaining = OpportunityComponent(
        "EXPECTED_REMAINING_MOVE",
        context.expected_remaining_move,
        ComponentAvailability.AVAILABLE if context.expected_remaining_move is not None else ComponentAvailability.UNAVAILABLE,
        context.expected_remaining_move_basis or "no gated point-in-time remaining-move estimate is available",
    )
    downside = OpportunityComponent(
        "EXPECTED_DOWNSIDE",
        context.expected_downside,
        ComponentAvailability.AVAILABLE if context.expected_downside is not None else ComponentAvailability.UNAVAILABLE,
        context.expected_downside_basis or "no gated point-in-time downside estimate is available",
    )
    economic_value = (
        context.continuation_probability * context.expected_remaining_move
        - context.expected_downside
        - features.spread_cost_per_share
        if all(value is not None for value in (
            context.continuation_probability,
            context.expected_remaining_move,
            context.expected_downside,
            features.spread_cost_per_share,
        ))
        else None
    )
    return (
        technical_component,
        OpportunityComponent(
            "ENTRY_PRICE_QUALITY",
            -features.price_drift_in_r if features.price_drift_in_r is not None else None,
            ComponentAvailability.AVAILABLE if features.price_drift_in_r is not None else ComponentAvailability.UNAVAILABLE,
            "negative drift in original R; higher is better" if features.price_drift_in_r is not None else "valid risk and complete quote required",
        ),
        OpportunityComponent(
            "RISK_GEOMETRY",
            features.current_entry_risk_multiple_vs_original,
            ComponentAvailability.AVAILABLE if features.current_entry_risk_multiple_vs_original is not None else ComponentAvailability.UNAVAILABLE,
            "current ask risk divided by original planned risk" if features.current_entry_risk_multiple_vs_original is not None else "valid risk and complete quote required",
        ),
        OpportunityComponent(
            "EXECUTION_FRICTION",
            features.spread_cost_in_r,
            ComponentAvailability.AVAILABLE if features.spread_cost_in_r is not None else ComponentAvailability.UNAVAILABLE,
            "L1 spread expressed in original R; round-trip cost remains unavailable" if features.spread_cost_in_r is not None else "complete L1 quote required",
        ),
        OpportunityComponent(
            "TIMING_DECAY",
            Decimal(features.remaining_validity_ms) if features.remaining_validity_ms is not None else None,
            ComponentAvailability.AVAILABLE if features.remaining_validity_ms is not None else ComponentAvailability.UNAVAILABLE,
            "milliseconds remaining in persisted entry validity" if features.remaining_validity_ms is not None else "entry validity boundary unavailable",
        ),
        remaining,
        continuation,
        downside,
        OpportunityComponent(
            "OPPORTUNITY_VALUE",
            economic_value,
            ComponentAvailability.AVAILABLE if economic_value is not None else ComponentAvailability.UNAVAILABLE,
            "P(continuation) × remaining move − downside − L1 spread"
            if economic_value is not None
            else "complete calibrated continuation, move, downside, and friction evidence required",
        ),
        OpportunityComponent(
            "DATA_QUALITY",
            ONE if quote_fresh else ZERO if quote_complete else None,
            ComponentAvailability.AVAILABLE if quote_complete else ComponentAvailability.UNAVAILABLE,
            "complete fresh L1 quote" if quote_fresh else "quote is stale" if quote_complete else "complete timestamped L1 quote unavailable",
        ),
    )


def _action(context, features, quote_fresh, policy):
    terminal = (context.order_terminal_state or "").upper()
    if terminal in {"DAY_EXPIRED", "ENTRY_STALE", "EXPIRED"} or (
        features.remaining_validity_ms is not None and features.remaining_validity_ms <= 0
    ):
        return ShadowAction.ABANDON_STALE, "persisted entry validity is exhausted"
    if terminal == "STRUCTURAL_STOP_INVALIDATED" or (
        context.ask is not None and context.ask <= context.structural_stop
    ):
        return ShadowAction.ABANDON_RISK_GEOMETRY, "structural stop is invalidated at the decision cutoff"
    if not quote_fresh or features.original_risk_per_share is None:
        return ShadowAction.INSUFFICIENT_EVIDENCE, "fresh complete L1 quote and valid original risk are required"
    if any(value is None for value in (
        context.continuation_probability,
        context.expected_remaining_move,
        context.expected_downside,
    )):
        return ShadowAction.INSUFFICIENT_EVIDENCE, "calibrated continuation, remaining move, and downside evidence are incomplete"
    assert features.spread_cost_per_share is not None
    value = (
        context.continuation_probability * context.expected_remaining_move
        - context.expected_downside
        - features.spread_cost_per_share
    )
    if value <= ZERO:
        if features.current_entry_risk_multiple_vs_original is not None and features.current_entry_risk_multiple_vs_original >= policy.material_risk_inflation:
            return ShadowAction.ABANDON_RISK_GEOMETRY, "complete research decomposition is non-positive and risk inflation is material"
        if features.price_drift_in_r is not None and features.price_drift_in_r >= policy.material_drift_in_r:
            return ShadowAction.ABANDON_PRICE_DRIFT, "complete research decomposition is non-positive and price drift is material"
        return ShadowAction.WAIT_FOR_RETRACE, "complete research decomposition is non-positive without structural invalidation"
    if features.price_drift_in_r is not None and features.price_drift_in_r > ZERO:
        return ShadowAction.REPRICE_CANDIDATE, "positive research decomposition at current L1; hypothetical only"
    return ShadowAction.KEEP_ORIGINAL_LIMIT, "positive research decomposition without adverse price drift"


def _reprice_candidates(context, features):
    if context.ask is None or context.ask <= context.structural_stop or features.current_risk_budget_quantity is None:
        return ()
    reward_risk = (
        context.expected_remaining_move / features.current_entry_risk_per_share
        if context.expected_remaining_move is not None and features.current_entry_risk_per_share
        else None
    )
    return (BoundedRepriceCandidate(
        derivation="DECISION_TIME_BEST_ASK",
        entry_price=context.ask,
        structural_stop=context.structural_stop,
        risk_per_share=features.current_entry_risk_per_share,
        unchanged_risk_budget_quantity=features.current_risk_budget_quantity,
        reward_risk=reward_risk,
    ),)


def _trend(previous, current):
    old_conf = previous.context.technical_confidence or previous.context.scanner_score
    new_conf = current.context.technical_confidence or current.context.scanner_score
    old_value = _component_value(previous, "ENTRY_PRICE_QUALITY")
    new_value = _component_value(current, "ENTRY_PRICE_QUALITY")
    if None in (old_conf, new_conf, old_value, new_value):
        return OpportunityTrend.UNAVAILABLE
    confidence = (new_conf > old_conf) - (new_conf < old_conf)
    entry = (new_value > old_value) - (new_value < old_value)
    if confidence > 0 and entry < 0:
        return OpportunityTrend.CONFIDENCE_UP_ENTRY_VALUE_DOWN
    if confidence > 0 and entry > 0:
        return OpportunityTrend.CONFIDENCE_UP_ENTRY_VALUE_UP
    if confidence < 0 and entry > 0:
        return OpportunityTrend.CONFIDENCE_DOWN_ENTRY_VALUE_UP
    if confidence < 0 and entry < 0:
        return OpportunityTrend.CONFIDENCE_DOWN_ENTRY_VALUE_DOWN
    return OpportunityTrend.UNCHANGED


def _component_value(observation, name):
    return next((item.value for item in observation.components if item.name == name), None)


def _identity(context, policy):
    memberships = ",".join(context.detector_memberships)
    material = "|".join((
        policy.version,
        context.symbol,
        context.lifecycle_id,
        context.decision_cutoff.isoformat(),
        str(context.planned_entry_price),
        str(context.structural_stop),
        str(context.bid),
        str(context.ask),
        memberships,
    ))
    return sha256(material.encode("utf-8")).hexdigest()


__all__ = ["EvaluationPolicy", "evaluate_entry_opportunity"]
