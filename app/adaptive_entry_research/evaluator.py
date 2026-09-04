"""Pure conservative evaluator; fast reassessment is not automatic chasing."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from hashlib import sha256

from .contracts import AdaptiveEntryRecommendation, EntryPlan, MaterialChangeReason, ShadowRecommendation, WorkingEntrySnapshot


@dataclass(frozen=True, slots=True)
class ReassessmentPolicy:
    keep_within_r: Decimal = Decimal("0.25")
    retrace_within_r: Decimal = Decimal("0.75")
    maximum_reprice_drift_r: Decimal = Decimal("1.50")
    maximum_risk_inflation: Decimal = Decimal("2.50")
    maximum_spread_percent: Decimal = Decimal("2.00")
    minimum_setup_quality: Decimal = Decimal("0.70")
    quote_stale_seconds: Decimal = Decimal("5")
    version: str = "ADAPTIVE_WORKING_ENTRY_RESEARCH_V1"


def resize_to_original_risk_budget(snapshot: WorkingEntrySnapshot, entry: Decimal, stop: Decimal) -> EntryPlan:
    risk = abs(entry - stop)
    if risk <= 0:
        return EntryPlan(entry, stop, None, None, None)
    # Filled exposure consumes its proportional share of the original budget.
    filled_exposure = max(snapshot.filled_quantity, snapshot.existing_position_quantity)
    available_budget = max(
        Decimal("0"),
        snapshot.original_total_risk - Decimal(filled_exposure) * snapshot.original_risk_per_share,
    )
    quantity = min(
        snapshot.remaining_quantity,
        int((available_budget / risk).to_integral_value(rounding=ROUND_FLOOR)),
    )
    return EntryPlan(entry, stop, quantity, risk, risk * quantity)


def evaluate_reassessment(
    snapshot: WorkingEntrySnapshot,
    reasons: tuple[MaterialChangeReason, ...],
    *,
    policy: ReassessmentPolicy = ReassessmentPolicy(),
) -> AdaptiveEntryRecommendation:
    market = snapshot.ask or snapshot.last or snapshot.bid
    drift = None if market is None else market - snapshot.original_limit_price
    drift_pct = None if drift is None else drift / snapshot.original_limit_price * 100
    drift_r = None if drift is None else drift / snapshot.original_risk_per_share
    fresh = EntryPlan(None, None, None, None, None)
    evidence: list[str] = []

    if snapshot.terminal_reason in {"ENTRY_STALE", "DAY_EXPIRED", "EXPIRED"} or snapshot.remaining_validity_seconds <= 0:
        recommendation = ShadowRecommendation.ABANDON_STALE
        evidence.append("EXISTING_STALE_BOUNDARY_REACHED")
    elif snapshot.setup_state in {"INVALIDATED", "FAILED", "REJECTED"} or snapshot.current_technical_actionable is False:
        recommendation = ShadowRecommendation.ABANDON_SETUP_INVALIDATED
        evidence.append("CURRENT_WARRIOR_STATE_NOT_ACTIONABLE")
    elif market is None or snapshot.quote_timestamp is None or snapshot.quote_freshness_seconds is None or snapshot.quote_freshness_seconds > policy.quote_stale_seconds:
        recommendation = ShadowRecommendation.INSUFFICIENT_EVIDENCE
        evidence.append("FRESH_L1_REQUIRED")
    elif drift_r is not None and drift_r >= Decimal("3"):
        recommendation = ShadowRecommendation.ABANDON_PRICE_DRIFT
        evidence.append("PRICE_DRIFT_AT_LEAST_3R")
    elif snapshot.current_reference_price is not None and snapshot.current_structural_stop is not None:
        fresh = resize_to_original_risk_budget(snapshot, snapshot.current_reference_price, snapshot.current_structural_stop)
        inflation = None if fresh.risk_per_share is None else fresh.risk_per_share / snapshot.original_risk_per_share
        if fresh.risk_per_share is None or fresh.quantity is None or fresh.quantity <= 0 or inflation is None or inflation > policy.maximum_risk_inflation:
            recommendation = ShadowRecommendation.ABANDON_RISK_GEOMETRY
            evidence.append("FRESH_RISK_GEOMETRY_UNACCEPTABLE")
        elif drift_r is not None and abs(drift_r) <= policy.keep_within_r:
            recommendation = ShadowRecommendation.KEEP_ORIGINAL_LIMIT
            evidence.append("ORIGINAL_LIMIT_WITHIN_TOLERANCE")
        elif drift_r is not None and drift_r <= policy.retrace_within_r:
            recommendation = ShadowRecommendation.WAIT_FOR_RETRACE
            evidence.append("DISPLACED_WITHIN_RETRACE_ZONE")
        elif (
            drift_r is not None and drift_r <= policy.maximum_reprice_drift_r
            and snapshot.current_technical_actionable is True
            and snapshot.current_setup_quality is not None
            and snapshot.current_setup_quality >= policy.minimum_setup_quality
            and snapshot.spread_percent is not None
            and snapshot.spread_percent <= policy.maximum_spread_percent
            and fresh.quantity != snapshot.original_quantity
        ):
            recommendation = ShadowRecommendation.REPRICE_AND_RESIZE_CANDIDATE
            evidence.extend(("FRESH_STRUCTURE_EXPLICIT", "RISK_BUDGET_RESIZED", "NOT_EXECUTION_AUTHORITY"))
        else:
            recommendation = ShadowRecommendation.WAIT_FOR_RETRACE
            evidence.append("REPRICE_GATES_INCOMPLETE")
    elif drift_r is not None and abs(drift_r) <= policy.keep_within_r:
        recommendation = ShadowRecommendation.KEEP_ORIGINAL_LIMIT
        evidence.append("ORIGINAL_LIMIT_WITHIN_TOLERANCE")
    elif drift_r is not None and drift_r <= policy.retrace_within_r:
        recommendation = ShadowRecommendation.WAIT_FOR_RETRACE
        evidence.append("DISPLACED_WITHIN_RETRACE_ZONE")
    else:
        recommendation = ShadowRecommendation.INSUFFICIENT_EVIDENCE
        evidence.append("EXPLICIT_FRESH_REFERENCE_AND_STOP_REQUIRED")

    inflation = None if fresh.risk_per_share is None else fresh.risk_per_share / snapshot.original_risk_per_share
    entry_delta = None if fresh.entry is None else fresh.entry - snapshot.original_limit_price
    stop_delta = None if fresh.stop is None else fresh.stop - snapshot.original_structural_stop
    quantity_delta = None if fresh.quantity is None else fresh.quantity - snapshot.original_quantity
    risk_delta = None if fresh.risk_per_share is None else fresh.risk_per_share - snapshot.original_risk_per_share
    total_risk_delta = None if fresh.total_risk is None else fresh.total_risk - snapshot.original_total_risk
    identity = sha256("|".join((policy.version, snapshot.order_id, snapshot.decision_cutoff.isoformat(), recommendation.value, ",".join(item.value for item in reasons))).encode()).hexdigest()
    return AdaptiveEntryRecommendation(
        identity, "1", snapshot.observed_at, snapshot.decision_cutoff,
        snapshot.symbol, snapshot.order_id, snapshot.strategy_lifecycle_id,
        reasons, recommendation,
        EntryPlan(snapshot.original_limit_price, snapshot.original_structural_stop, snapshot.original_quantity, snapshot.original_risk_per_share, snapshot.original_total_risk),
        fresh, drift, drift_pct, drift_r, inflation,
        entry_delta, stop_delta, quantity_delta, risk_delta, total_risk_delta,
        None if snapshot.bid is None else snapshot.original_limit_price - snapshot.bid,
        None if snapshot.ask is None else snapshot.original_limit_price - snapshot.ask,
        None if fresh.entry is None or snapshot.bid is None else fresh.entry - snapshot.bid,
        None if fresh.entry is None or snapshot.ask is None else fresh.entry - snapshot.ask,
        snapshot.remaining_validity_seconds, snapshot.existing_position_quantity,
        snapshot.remaining_quantity, tuple(evidence), snapshot.unavailable_evidence,
        snapshot.spread,
    )


__all__ = ["ReassessmentPolicy", "evaluate_reassessment", "resize_to_original_risk_budget"]
