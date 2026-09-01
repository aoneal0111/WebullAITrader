"""Future-only deterministic outcome labeling, separate from frozen snapshots."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from .models import (
    HORIZONS_MINUTES,
    AtlasDecision,
    HorizonOutcome,
    MissedOpportunityClassification,
    OutcomeStatus,
    OutcomeKind,
    PriceBar,
    TradeOpportunityExperience,
)

ONE_MINUTE = timedelta(minutes=1)
HUNDRED = Decimal("100")


class OutcomeEngine:
    """Labels completed 1-minute OHLC paths with conservative stop-first rules."""

    def evaluate(
        self,
        experience: TradeOpportunityExperience,
        bars: tuple[PriceBar, ...],
        *,
        finalize_missing: bool = False,
    ) -> tuple[HorizonOutcome, ...]:
        cutoff = experience.snapshot.decision_timestamp
        first_possible = cutoff.replace(second=0, microsecond=0)
        if cutoff > first_possible:
            first_possible += ONE_MINUTE
        ordered = tuple(sorted(
            (bar for bar in bars
             if bar.symbol.strip().upper() == experience.key.symbol.strip().upper()
             and bar.timestamp >= first_possible),
            key=lambda item: item.timestamp,
        ))
        outcomes = []
        for horizon in HORIZONS_MINUTES:
            target = cutoff + timedelta(minutes=horizon)
            sample = next((bar for bar in ordered if bar.timestamp + ONE_MINUTE >= target), None)
            if sample is None:
                if finalize_missing:
                    outcomes.append(self._missing(experience, horizon, target, "NO_COMPLETED_BAR_AT_HORIZON"))
                continue
            if sample.timestamp + ONE_MINUTE > target + ONE_MINUTE:
                outcomes.append(self._missing(experience, horizon, target, "HORIZON_GAP"))
                continue
            path = tuple(bar for bar in ordered if bar.timestamp <= sample.timestamp)
            expected = first_possible
            if not path or any(bar.timestamp != expected + index * ONE_MINUTE for index, bar in enumerate(path)):
                outcomes.append(self._missing(experience, horizon, target, "NONCONTIGUOUS_MINUTE_BARS"))
                continue
            outcomes.append(self._complete(experience, horizon, target, sample, path))
        return tuple(outcomes)

    def _complete(self, exp, horizon, target, sample, path):
        reference = exp.snapshot.reference_price or exp.snapshot.last_price
        if reference is None:
            raise ValueError("technical outcome requires an authoritative reference price")
        highest = max(item.high for item in path)
        lowest = min(item.low for item in path)
        mfe = highest - reference
        mae = lowest - reference
        entry = exp.snapshot.trigger_price
        stop = exp.snapshot.structural_stop
        risk = exp.snapshot.risk_per_share
        plan = _plan_path(path, exp.snapshot.decision_timestamp, entry, stop, risk)
        return HorizonOutcome(
            exp.experience_id, horizon, target, OutcomeStatus.COMPLETE,
            future_price=sample.close,
            return_percent=(sample.close - reference) / reference * HUNDRED,
            mfe=mfe, mae=mae,
            mfe_r=None if risk is None else mfe / risk,
            mae_r=None if risk is None else mae / risk,
            reached_1r=plan[0], reached_2r=plan[1], reached_3r=plan[2],
            stop_reached=plan[3], time_to_1r_seconds=plan[4],
            time_to_2r_seconds=plan[5], time_to_3r_seconds=plan[6],
            time_to_stop_seconds=plan[7], first_plan_event=plan[8],
            outcome_as_of=sample.timestamp + ONE_MINUTE,
            plan_outcome_kind=(
                OutcomeKind.HYPOTHETICAL_EXECUTION if risk is not None else None
            ),
        )

    @staticmethod
    def _missing(exp, horizon, target, reason):
        return HorizonOutcome(
            exp.experience_id, horizon, target, OutcomeStatus.INSUFFICIENT_DATA,
            unavailable_reason=reason,
        )


def _plan_path(path, cutoff, entry, stop, risk):
    if entry is None or stop is None or risk is None:
        return (None, None, None, None, None, None, None, None, None)
    hit = {1: None, 2: None, 3: None}
    stop_time = None
    first = None
    entered = False
    for bar in path:
        elapsed = int(((bar.timestamp + ONE_MINUTE) - cutoff).total_seconds())
        # Same-bar ambiguity is conservative: stop dominates entry and targets.
        if not entered:
            if bar.low <= stop and bar.high >= entry:
                stop_time, first = elapsed, "STOP"
                break
            if bar.high >= entry:
                entered = True
        if not entered:
            continue
        if bar.low <= stop:
            stop_time = elapsed
            first = first or "STOP"
            break
        for multiple in (1, 2, 3):
            if hit[multiple] is None and bar.high >= entry + risk * multiple:
                hit[multiple] = elapsed
                first = first or f"{multiple}R"
    return (
        hit[1] is not None, hit[2] is not None, hit[3] is not None,
        stop_time is not None, hit[1], hit[2], hit[3], stop_time, first,
    )


def classify_missed_opportunity(
    experience: TradeOpportunityExperience,
    outcome: HorizonOutcome | None,
    *, profitable_threshold_r: Decimal = Decimal("2"),
) -> MissedOpportunityClassification:
    """Classify non-entered plans using the longest complete horizon.

    PROFITABLE: >=2R before stop. PROTECTED: stop is first plan event and no 1R.
    DANGEROUS: 1R occurs but stop later occurs before 2R. NEUTRAL: complete path
    reaches neither stop nor 2R. Missing/planless data is insufficient.
    """

    if experience.actually_traded:
        return MissedOpportunityClassification.NOT_APPLICABLE
    if outcome is None or outcome.status is not OutcomeStatus.COMPLETE:
        return MissedOpportunityClassification.INSUFFICIENT_OUTCOME_DATA
    if experience.snapshot.risk_per_share is None:
        return MissedOpportunityClassification.INSUFFICIENT_OUTCOME_DATA
    threshold_hit = {
        Decimal("1"): outcome.reached_1r,
        Decimal("2"): outcome.reached_2r,
        Decimal("3"): outcome.reached_3r,
    }.get(profitable_threshold_r, False)
    if threshold_hit and outcome.first_plan_event != "STOP":
        return MissedOpportunityClassification.PROFITABLE_MISSED_OPPORTUNITY
    if outcome.first_plan_event == "STOP" and not outcome.reached_1r:
        return MissedOpportunityClassification.PROTECTED_REJECTION
    if outcome.reached_1r and outcome.stop_reached and not outcome.reached_2r:
        return MissedOpportunityClassification.DANGEROUS_FALSE_POSITIVE
    return MissedOpportunityClassification.NEUTRAL_REJECTION
