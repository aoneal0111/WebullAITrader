"""Forward labels kept strictly separate from decision-time shadow features."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .models import EntryOpportunityValueObservation


@dataclass(frozen=True, slots=True)
class ForwardPricePoint:
    timestamp: datetime
    last: Decimal
    bid: Decimal | None = None
    ask: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("forward price timestamp must be timezone-aware")
        if self.last <= 0 or any(
            value is not None and value <= 0
            for value in (self.bid, self.ask, self.high, self.low)
        ):
            raise ValueError("forward prices must be positive")
        if self.bid is not None and self.ask is not None and self.ask < self.bid:
            raise ValueError("forward ask cannot be below bid")


@dataclass(frozen=True, slots=True)
class PlanOutcome:
    plan: str
    entry_reference: Decimal
    risk_per_share: Decimal | None
    hypothetical_fillable: bool | None
    mfe: Decimal | None
    mae: Decimal | None
    mfe_r: Decimal | None
    mae_r: Decimal | None
    reached_1r: bool | None
    reached_2r: bool | None
    reached_3r: bool | None
    stop_first: bool | None
    target_first: bool | None
    time_to_mfe_ms: int | None
    time_to_mae_ms: int | None
    return_5m: Decimal | None
    return_15m: Decimal | None
    return_30m: Decimal | None


@dataclass(frozen=True, slots=True)
class ForwardOutcomeLabels:
    observation_id: str
    decision_cutoff: datetime
    labeled_at: datetime
    original_plan_outcome: PlanOutcome
    current_market_entry_outcome: PlanOutcome | None
    bounded_reprice_hypothesis_outcomes: tuple[PlanOutcome, ...]
    actual_fill_status: str | None
    actual_trade_status: str | None
    labels_only: bool = True
    research_only: bool = True
    execution_authorized: bool = False

    def __post_init__(self) -> None:
        if not self.labels_only or not self.research_only or self.execution_authorized:
            raise ValueError("forward outcomes are non-authoritative labels only")


def label_forward_outcomes(
    observation: EntryOpportunityValueObservation,
    points: tuple[ForwardPricePoint, ...],
    *,
    actual_fill_status: str | None = None,
    actual_trade_status: str | None = None,
) -> ForwardOutcomeLabels:
    """Label three counterfactual families without changing the observation."""

    if not points:
        raise ValueError("at least one forward price point is required")
    previous = observation.context.decision_cutoff
    for point in points:
        if point.timestamp <= observation.context.decision_cutoff:
            raise ValueError("forward labels must occur after the decision cutoff")
        if point.timestamp <= previous:
            raise ValueError("forward price points must be strictly ordered")
        previous = point.timestamp

    context = observation.context
    original = _plan_outcome(
        "ORIGINAL_PLAN_OUTCOME",
        context.planned_entry_price,
        context.structural_stop,
        points,
        context.decision_cutoff,
        limit_fill=True,
        marketable_at_cutoff=observation.features.original_limit_marketable,
    )
    current = (
        _plan_outcome(
            "CURRENT_MARKET_ENTRY_OUTCOME",
            context.ask,
            context.structural_stop,
            points,
            context.decision_cutoff,
            limit_fill=False,
            marketable_at_cutoff=True,
        )
        if context.ask is not None
        else None
    )
    bounded = tuple(
        _plan_outcome(
            "BOUNDED_REPRICE_HYPOTHESIS_OUTCOME",
            candidate.entry_price,
            candidate.structural_stop,
            points,
            context.decision_cutoff,
            limit_fill=False,
            marketable_at_cutoff=True,
        )
        for candidate in observation.reprice_candidates
    )
    return ForwardOutcomeLabels(
        observation_id=observation.observation_id,
        decision_cutoff=context.decision_cutoff,
        labeled_at=points[-1].timestamp,
        original_plan_outcome=original,
        current_market_entry_outcome=current,
        bounded_reprice_hypothesis_outcomes=bounded,
        actual_fill_status=actual_fill_status,
        actual_trade_status=actual_trade_status,
    )


def _plan_outcome(plan, entry, stop, points, cutoff, *, limit_fill, marketable_at_cutoff):
    risk = entry - stop
    valid_risk = risk > 0
    fill_index = 0 if marketable_at_cutoff else None
    if limit_fill and fill_index is None:
        fill_index = next(
            (index for index, point in enumerate(points) if (point.low or point.last) <= entry),
            None,
        )
    fillable = fill_index is not None
    if not fillable:
        return PlanOutcome(plan, entry, risk if valid_risk else None, False, None, None, None, None,
                           None, None, None, None, None, None, None, None, None, None)
    active = points[fill_index:]
    favorable = tuple((point.high or point.last) - entry for point in active)
    adverse = tuple((point.low or point.last) - entry for point in active)
    mfe = max(favorable)
    mae = min(adverse)
    mfe_index = favorable.index(mfe)
    mae_index = adverse.index(mae)
    stop_index = next((i for i, value in enumerate(adverse) if value <= -risk), None) if valid_risk else None
    target_index = next((i for i, value in enumerate(favorable) if value >= risk), None) if valid_risk else None
    return PlanOutcome(
        plan=plan,
        entry_reference=entry,
        risk_per_share=risk if valid_risk else None,
        hypothetical_fillable=True,
        mfe=mfe,
        mae=mae,
        mfe_r=mfe / risk if valid_risk else None,
        mae_r=mae / risk if valid_risk else None,
        reached_1r=mfe >= risk if valid_risk else None,
        reached_2r=mfe >= risk * 2 if valid_risk else None,
        reached_3r=mfe >= risk * 3 if valid_risk else None,
        stop_first=(stop_index is not None and (target_index is None or stop_index < target_index)) if valid_risk else None,
        target_first=(target_index is not None and (stop_index is None or target_index < stop_index)) if valid_risk else None,
        time_to_mfe_ms=int((active[mfe_index].timestamp - cutoff).total_seconds() * 1000),
        time_to_mae_ms=int((active[mae_index].timestamp - cutoff).total_seconds() * 1000),
        return_5m=_return_at(active, entry, cutoff + timedelta(minutes=5)),
        return_15m=_return_at(active, entry, cutoff + timedelta(minutes=15)),
        return_30m=_return_at(active, entry, cutoff + timedelta(minutes=30)),
    )


def _return_at(points, entry, target):
    point = next((item for item in points if item.timestamp >= target), None)
    return None if point is None else (point.last - entry) / entry


__all__ = [
    "ForwardOutcomeLabels",
    "ForwardPricePoint",
    "PlanOutcome",
    "label_forward_outcomes",
]
