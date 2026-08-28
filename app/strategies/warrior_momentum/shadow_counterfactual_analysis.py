"""Pure counterfactual research over immutable Warrior shadow evidence.

Each policy removes exactly one captured blocker for analytical attribution.
This module has no strategy runtime, execution, gateway, broker, or authorization
dependency and cannot create an executable signal.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
import json
from statistics import median
from typing import Iterable, Mapping, Sequence

from .shadow_policy_analysis import (
    AnalyticalOpportunity,
    COMPLETE,
    GROUPING_METHOD,
    ShadowCaptureDataset,
    ShadowEvaluation,
    ShadowOutcome,
    ShadowPolicyAnalysisConfiguration,
    group_analytical_opportunities,
)


class CounterfactualPolicy(StrEnum):
    IGNORE_NO_CATALYST = "IGNORE_NO_CATALYST"
    IGNORE_RVOL_LOW = "IGNORE_RVOL_LOW"
    IGNORE_FLOAT_HIGH = "IGNORE_FLOAT_HIGH"
    IGNORE_RISK_REJECTED = "IGNORE_RISK_REJECTED"

    @property
    def ignored_blocker(self) -> str:
        return self.removeprefix("IGNORE_")


POLICIES = tuple(CounterfactualPolicy)


class CounterfactualStatus(StrEnum):
    NOT_APPLICABLE_SELECTED_BLOCKER_ABSENT = (
        "NOT_APPLICABLE_SELECTED_BLOCKER_ABSENT"
    )
    NOT_COUNTERFACTUAL_ENTRY_READY = "NOT_COUNTERFACTUAL_ENTRY_READY"
    COUNTERFACTUAL_ENTRY_READY = "COUNTERFACTUAL_ENTRY_READY"
    COUNTERFACTUAL_POLICY_ELIGIBLE_RISK_UNAPPROVED = (
        "COUNTERFACTUAL_POLICY_ELIGIBLE_RISK_UNAPPROVED"
    )


class CounterfactualOutcome(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    COUNTERFACTUAL_WIN_1R = "COUNTERFACTUAL_WIN_1R"
    COUNTERFACTUAL_WIN_2R = "COUNTERFACTUAL_WIN_2R"
    COUNTERFACTUAL_STOP = "COUNTERFACTUAL_STOP"
    COUNTERFACTUAL_UNRESOLVED = "COUNTERFACTUAL_UNRESOLVED"
    COUNTERFACTUAL_NEVER_TRIGGERED = "COUNTERFACTUAL_NEVER_TRIGGERED"
    COUNTERFACTUAL_INCOMPLETE_DATA = "COUNTERFACTUAL_INCOMPLETE_DATA"


@dataclass(frozen=True, slots=True)
class CounterfactualEvaluationResult:
    policy: CounterfactualPolicy
    evaluation_id: str
    opportunity_id: str
    symbol: str
    timestamp: datetime
    session: str
    authoritative_blockers: tuple[str, ...]
    counterfactual_remaining_blockers: tuple[str, ...]
    setup_type: str | None
    setup_state: str | None
    trigger: Decimal | None
    stop: Decimal | None
    warrior_status: str | None
    authoritative_plan_valid: bool
    counterfactual_status: CounterfactualStatus
    eligibility_changed: bool
    counterfactual_entry_ready: bool
    execution_authorized: bool
    catalyst_state: str | None
    catalyst_evidence_cohort: str
    outcome_available: bool
    sufficient_outcome_evidence: bool
    horizons: Mapping[int, Mapping[str, object]]
    trigger_crossed: bool
    stop_hit: bool
    one_r_reached: bool
    two_r_reached: bool
    maximum_mfe: Decimal | None
    minimum_mae: Decimal | None
    authoritative_classification: str
    counterfactual_outcome: CounterfactualOutcome


@dataclass(frozen=True, slots=True)
class CounterfactualPolicySummary:
    policy: CounterfactualPolicy
    blocker: str
    raw_evaluations_containing_blocker: int
    unique_opportunities_containing_blocker: int
    blocker_symbol_count: int
    blocker_session_count: int
    triggered_plan_evaluations: int
    triggered_plan_opportunities: int
    triggered_symbol_count: int
    triggered_session_count: int
    sole_blocker_triggered_plans: int
    sole_blocker_triggered_opportunities: int
    confounded_triggered_plans: int
    confounded_triggered_opportunities: int
    eligibility_changes: int
    eligibility_change_opportunities: int
    sufficient_outcome_entries: int
    resolved_outcomes: int
    one_r_wins: int
    two_r_wins: int
    stops: int
    unresolved: int
    never_triggered: int
    incomplete: int
    median_mfe: Decimal | None
    median_mae: Decimal | None
    cross_symbol_count: int
    cross_session_count: int
    evidence_score: int
    recommendation: str
    evidence_notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CounterfactualResearchReport:
    generated_at: datetime | None
    data_cutoff: datetime | None
    grouping_method: str
    evaluation_count: int
    outcome_count: int
    unique_opportunity_count: int
    results: tuple[CounterfactualEvaluationResult, ...]
    policies: Mapping[CounterfactualPolicy, CounterfactualPolicySummary]


class ShadowCounterfactualAnalyzer:
    """Remove one captured blocker without re-running or mutating strategy state."""

    def __init__(
        self,
        config: ShadowPolicyAnalysisConfiguration = ShadowPolicyAnalysisConfiguration(),
    ) -> None:
        self.config = config

    def analyze(
        self, dataset: ShadowCaptureDataset, *, generated_at: datetime | None = None,
    ) -> CounterfactualResearchReport:
        evaluations = tuple(sorted(dataset.evaluations, key=lambda item: item.timestamp))
        valid_ids = {item.record_id for item in evaluations}
        outcomes = tuple(
            item for item in dataset.outcomes if item.evaluation_record_id in valid_ids
        )
        outcomes_by_evaluation: dict[str, list[ShadowOutcome]] = defaultdict(list)
        for outcome in outcomes:
            outcomes_by_evaluation[outcome.evaluation_record_id].append(outcome)
        opportunities = group_analytical_opportunities(evaluations, self.config)
        opportunity_by_evaluation = {
            evaluation_id: opportunity
            for opportunity in opportunities
            for evaluation_id in opportunity.evaluation_ids
        }
        results = tuple(
            self.evaluate(
                policy, evaluation,
                tuple(outcomes_by_evaluation.get(evaluation.record_id, ())),
                opportunity_by_evaluation[evaluation.record_id],
            )
            for policy in POLICIES
            for evaluation in evaluations
        )
        summaries = {
            policy: _policy_summary(
                policy,
                tuple(item for item in results if item.policy is policy),
            )
            for policy in POLICIES
        }
        return CounterfactualResearchReport(
            generated_at or dataset.data_cutoff,
            dataset.data_cutoff,
            GROUPING_METHOD,
            len(evaluations),
            len(outcomes),
            len(opportunities),
            results,
            summaries,
        )

    def evaluate(
        self,
        policy: CounterfactualPolicy,
        evaluation: ShadowEvaluation,
        outcomes: Sequence[ShadowOutcome],
        opportunity: AnalyticalOpportunity,
    ) -> CounterfactualEvaluationResult:
        blockers = _blockers(evaluation)
        selected = policy.ignored_blocker
        selected_present = selected in blockers
        remaining = tuple(item for item in blockers if item != selected)
        setup_type = _optional_string(evaluation.payload.get("setup_type"))
        setup_state = _optional_string(evaluation.payload.get("setup_state"))
        trigger = _optional_decimal(evaluation.payload.get("trigger"))
        stop = _optional_decimal(evaluation.payload.get("stop"))
        plan_valid = bool(
            evaluation.payload.get("counterfactual_entry_valid")
            and setup_type is not None
            and setup_state == "TRIGGERED"
            and trigger is not None
            and stop is not None
            and trigger > stop
        )
        if not selected_present:
            status = CounterfactualStatus.NOT_APPLICABLE_SELECTED_BLOCKER_ABSENT
        elif remaining or not plan_valid:
            status = CounterfactualStatus.NOT_COUNTERFACTUAL_ENTRY_READY
        elif policy is CounterfactualPolicy.IGNORE_RISK_REJECTED:
            status = (
                CounterfactualStatus.COUNTERFACTUAL_POLICY_ELIGIBLE_RISK_UNAPPROVED
            )
        else:
            status = CounterfactualStatus.COUNTERFACTUAL_ENTRY_READY
        changed = status in {
            CounterfactualStatus.COUNTERFACTUAL_ENTRY_READY,
            CounterfactualStatus.COUNTERFACTUAL_POLICY_ELIGIBLE_RISK_UNAPPROVED,
        }
        complete = tuple(
            item for item in outcomes if item.payload.get("status") == COMPLETE
        )
        horizons = {
            int(item.payload["horizon_minutes"]): {
                key: item.payload.get(key) for key in (
                    "status", "classification", "return_percent", "mfe_percent",
                    "mae_percent", "hypothetical_trade",
                )
            }
            for item in sorted(
                outcomes, key=lambda value: int(value.payload["horizon_minutes"]),
            )
        }
        plans = tuple(
            item.payload.get("hypothetical_trade") for item in complete
            if isinstance(item.payload.get("hypothetical_trade"), Mapping)
        )
        trigger_crossed = any(_plan_trigger_crossed(plan) for plan in plans)
        stop_hit = any(plan.get("stop_hit_at_bar") is not None for plan in plans)
        multiples = _effective_reward_multiples(plans)
        one_r = any(value >= 1 for value in multiples)
        two_r = any(value >= 2 for value in multiples)
        expected_horizons = tuple(
            int(value) for value in evaluation.payload.get(
                "horizons_minutes", (1, 2, 5, 10),
            )
        )
        complete_horizons = {
            int(item.payload["horizon_minutes"]) for item in complete
        }
        sufficient = bool(expected_horizons) and set(expected_horizons) <= complete_horizons
        counterfactual_outcome = _counterfactual_outcome(
            changed=changed,
            complete=complete,
            sufficient=sufficient,
            plans=plans,
            trigger_crossed=trigger_crossed,
            stop_hit=stop_hit,
            one_r=one_r,
            two_r=two_r,
        )
        classifications = tuple(
            str(item.payload.get("classification")) for item in complete
        )
        return CounterfactualEvaluationResult(
            policy=policy,
            evaluation_id=evaluation.record_id,
            opportunity_id=opportunity.opportunity_id,
            symbol=evaluation.symbol,
            timestamp=evaluation.timestamp,
            session=str(evaluation.payload.get("session") or "UNKNOWN"),
            authoritative_blockers=blockers,
            counterfactual_remaining_blockers=remaining,
            setup_type=setup_type,
            setup_state=setup_state,
            trigger=trigger,
            stop=stop,
            warrior_status=_optional_string(evaluation.payload.get("warrior_status")),
            authoritative_plan_valid=plan_valid,
            counterfactual_status=status,
            eligibility_changed=changed,
            counterfactual_entry_ready=(
                status is CounterfactualStatus.COUNTERFACTUAL_ENTRY_READY
            ),
            execution_authorized=False,
            catalyst_state=_optional_string(evaluation.payload.get("catalyst_state")),
            catalyst_evidence_cohort=_catalyst_evidence_cohort(
                evaluation.payload.get("catalyst_state")
            ),
            outcome_available=bool(complete),
            sufficient_outcome_evidence=sufficient,
            horizons=horizons,
            trigger_crossed=trigger_crossed,
            stop_hit=stop_hit,
            one_r_reached=one_r,
            two_r_reached=two_r,
            maximum_mfe=max(_decimal_values(complete, "mfe_percent"), default=None),
            minimum_mae=min(_decimal_values(complete, "mae_percent"), default=None),
            authoritative_classification=_strongest_classification(classifications),
            counterfactual_outcome=counterfactual_outcome,
        )


def _counterfactual_outcome(
    *,
    changed: bool,
    complete: Sequence[ShadowOutcome],
    sufficient: bool,
    plans: Sequence[Mapping[str, object]],
    trigger_crossed: bool,
    stop_hit: bool,
    one_r: bool,
    two_r: bool,
) -> CounterfactualOutcome:
    if not changed:
        return CounterfactualOutcome.NOT_APPLICABLE
    if two_r:
        return CounterfactualOutcome.COUNTERFACTUAL_WIN_2R
    if one_r:
        return CounterfactualOutcome.COUNTERFACTUAL_WIN_1R
    if stop_hit:
        return CounterfactualOutcome.COUNTERFACTUAL_STOP
    if not complete or not sufficient:
        return CounterfactualOutcome.COUNTERFACTUAL_INCOMPLETE_DATA
    if plans and all(str(plan.get("state")) == "NEVER_TRIGGERED" for plan in plans):
        return CounterfactualOutcome.COUNTERFACTUAL_NEVER_TRIGGERED
    if trigger_crossed:
        return CounterfactualOutcome.COUNTERFACTUAL_UNRESOLVED
    return CounterfactualOutcome.COUNTERFACTUAL_NEVER_TRIGGERED


def _policy_summary(
    policy: CounterfactualPolicy,
    results: Sequence[CounterfactualEvaluationResult],
) -> CounterfactualPolicySummary:
    containing = tuple(
        item for item in results if policy.ignored_blocker in item.authoritative_blockers
    )
    triggered = tuple(item for item in containing if _result_has_valid_plan(item))
    sole = tuple(item for item in triggered if len(item.authoritative_blockers) == 1)
    confounded = tuple(item for item in triggered if len(item.authoritative_blockers) > 1)
    changed = tuple(item for item in containing if item.eligibility_changed)
    outcomes = defaultdict(int)
    for item in changed:
        outcomes[item.counterfactual_outcome] += 1
    changed_opportunities = {item.opportunity_id for item in changed}
    winning_opportunities = {
        item.opportunity_id for item in changed
        if item.counterfactual_outcome in {
            CounterfactualOutcome.COUNTERFACTUAL_WIN_1R,
            CounterfactualOutcome.COUNTERFACTUAL_WIN_2R,
        }
    }
    resolved_opportunities = {
        item.opportunity_id for item in changed
        if item.counterfactual_outcome in {
            CounterfactualOutcome.COUNTERFACTUAL_WIN_1R,
            CounterfactualOutcome.COUNTERFACTUAL_WIN_2R,
            CounterfactualOutcome.COUNTERFACTUAL_STOP,
            CounterfactualOutcome.COUNTERFACTUAL_NEVER_TRIGGERED,
        }
    }
    symbols = {item.symbol for item in changed}
    sessions = {item.session for item in changed}
    score = (
        18 * len(changed_opportunities)
        + 18 * len(winning_opportunities)
        + 6 * len(resolved_opportunities)
        + 8 * len({item.opportunity_id for item in sole if item.eligibility_changed})
        + min(10, 3 * len(symbols))
        + min(8, 4 * len(sessions))
    )
    notes: list[str] = []
    if not changed:
        notes.append("no sole-blocker authoritative triggered plan changed eligibility")
    if len(changed_opportunities) == 1:
        score -= 12
        notes.append("evidence is limited to one opportunity")
    if changed and len(symbols) == 1:
        score -= 8
        notes.append("evidence is limited to one symbol")
    if changed and len(sessions) == 1:
        score -= 4
        notes.append("evidence is limited to one session")
    incomplete = outcomes[CounterfactualOutcome.COUNTERFACTUAL_INCOMPLETE_DATA]
    score -= min(20, incomplete * 4)
    if incomplete:
        notes.append(f"{incomplete} changed evaluations have incomplete outcomes")
    price_only = sum(
        item.authoritative_classification == "MISSED_OPPORTUNITY_PRICE_MOVE_ONLY"
        for item in changed
    )
    score -= min(20, price_only * 4)
    if price_only:
        notes.append(f"{price_only} changed evaluations are price-move-only evidence")
    score = max(0, score)
    wins = (
        outcomes[CounterfactualOutcome.COUNTERFACTUAL_WIN_1R]
        + outcomes[CounterfactualOutcome.COUNTERFACTUAL_WIN_2R]
    )
    stops = outcomes[CounterfactualOutcome.COUNTERFACTUAL_STOP]
    if score >= 80:
        recommendation = "STRONG_RESEARCH_CANDIDATE"
    elif score >= 35:
        recommendation = "CONTINUE_RESEARCH"
    elif len(resolved_opportunities) >= 3 and stops > wins:
        recommendation = "KEEP_STRICT"
    else:
        recommendation = "INSUFFICIENT_EVIDENCE"
    mfe = tuple(item.maximum_mfe for item in changed if item.maximum_mfe is not None)
    mae = tuple(item.minimum_mae for item in changed if item.minimum_mae is not None)
    return CounterfactualPolicySummary(
        policy=policy,
        blocker=policy.ignored_blocker,
        raw_evaluations_containing_blocker=len(containing),
        unique_opportunities_containing_blocker=len({
            item.opportunity_id for item in containing
        }),
        blocker_symbol_count=len({item.symbol for item in containing}),
        blocker_session_count=len({item.session for item in containing}),
        triggered_plan_evaluations=len(triggered),
        triggered_plan_opportunities=len({item.opportunity_id for item in triggered}),
        triggered_symbol_count=len({item.symbol for item in triggered}),
        triggered_session_count=len({item.session for item in triggered}),
        sole_blocker_triggered_plans=len(sole),
        sole_blocker_triggered_opportunities=len({item.opportunity_id for item in sole}),
        confounded_triggered_plans=len(confounded),
        confounded_triggered_opportunities=len({
            item.opportunity_id for item in confounded
        }),
        eligibility_changes=len(changed),
        eligibility_change_opportunities=len(changed_opportunities),
        sufficient_outcome_entries=sum(
            item.sufficient_outcome_evidence for item in changed
        ),
        resolved_outcomes=sum(
            item.counterfactual_outcome in {
                CounterfactualOutcome.COUNTERFACTUAL_WIN_1R,
                CounterfactualOutcome.COUNTERFACTUAL_WIN_2R,
                CounterfactualOutcome.COUNTERFACTUAL_STOP,
                CounterfactualOutcome.COUNTERFACTUAL_NEVER_TRIGGERED,
            }
            for item in changed
        ),
        one_r_wins=sum(item.one_r_reached for item in changed),
        two_r_wins=sum(item.two_r_reached for item in changed),
        stops=stops,
        unresolved=outcomes[CounterfactualOutcome.COUNTERFACTUAL_UNRESOLVED],
        never_triggered=outcomes[
            CounterfactualOutcome.COUNTERFACTUAL_NEVER_TRIGGERED
        ],
        incomplete=incomplete,
        median_mfe=median(mfe) if mfe else None,
        median_mae=median(mae) if mae else None,
        cross_symbol_count=len(symbols),
        cross_session_count=len(sessions),
        evidence_score=score,
        recommendation=recommendation,
        evidence_notes=tuple(notes),
    )


def render_counterfactual_report(report: CounterfactualResearchReport) -> str:
    lines = [
        "ATLAS CATALYST-FIRST COUNTERFACTUAL SHADOW POLICY RESEARCH",
        f"data cutoff: {report.data_cutoff}",
        f"evaluations/outcomes/opportunities: {report.evaluation_count}/"
        f"{report.outcome_count}/{report.unique_opportunity_count}",
        f"grouping: {report.grouping_method}",
        "",
        "POLICY MATRIX",
        "policy | changed | changed opportunities | sufficient | resolved | 1R | 2R | "
        "stops | unresolved | never | incomplete | evidence",
    ]
    for policy in POLICIES:
        item = report.policies[policy]
        lines.append(
            f"{policy.value} | {item.eligibility_changes} | "
            f"{item.eligibility_change_opportunities} | "
            f"{item.sufficient_outcome_entries} | {item.resolved_outcomes} | "
            f"{item.one_r_wins} | "
            f"{item.two_r_wins} | {item.stops} | {item.unresolved} | "
            f"{item.never_triggered} | {item.incomplete} | "
            f"{item.evidence_score}/{item.recommendation}"
        )
    lines.extend(("", "POLICY COHORTS"))
    for policy in POLICIES:
        item = report.policies[policy]
        lines.append(
            f"{policy.value}: raw={item.raw_evaluations_containing_blocker}, "
            f"opportunities={item.unique_opportunities_containing_blocker}, "
            f"triggered={item.triggered_plan_evaluations}/"
            f"{item.triggered_plan_opportunities}, sole="
            f"{item.sole_blocker_triggered_plans}/"
            f"{item.sole_blocker_triggered_opportunities}, confounded="
            f"{item.confounded_triggered_plans}/"
            f"{item.confounded_triggered_opportunities}, blocker symbols/sessions="
            f"{item.blocker_symbol_count}/{item.blocker_session_count}, "
            f"changed symbols/sessions={item.cross_symbol_count}/"
            f"{item.cross_session_count}"
        )
    lines.extend(("", "TRIGGERED PLAN RESULTS"))
    for item in report.results:
        if not _result_has_valid_plan(item) or (
            item.policy.ignored_blocker not in item.authoritative_blockers
        ):
            continue
        lines.append(
            f"{item.policy.value} {item.symbol} {item.timestamp}: authoritative="
            f"{'+'.join(item.authoritative_blockers)} remaining="
            f"{'+'.join(item.counterfactual_remaining_blockers) or 'NONE'} "
            f"status={item.counterfactual_status.value} "
            f"outcome={item.counterfactual_outcome.value}"
        )
    lines.extend((
        "", "CATALYST HYPOTHESIS",
        "CATALYST_SOLE_BLOCKER_TRIGGERED_PLAN="
        f"{report.policies[CounterfactualPolicy.IGNORE_NO_CATALYST].sole_blocker_triggered_plans}",
        "CATALYST_CONFOUNDED_TRIGGERED_PLAN="
        f"{report.policies[CounterfactualPolicy.IGNORE_NO_CATALYST].confounded_triggered_plans}",
        "", "FINAL RESEARCH CONCLUSION",
        "NO_CATALYST IS NOT YET PROVEN TOO STRICT",
    ))
    return "\n".join(lines)


def counterfactual_report_as_json(report: CounterfactualResearchReport) -> str:
    return json.dumps(asdict(report), default=_json_default, sort_keys=True, indent=2)


def _result_has_valid_plan(item: CounterfactualEvaluationResult) -> bool:
    return bool(
        item.authoritative_plan_valid
        and item.setup_type is not None
        and item.setup_state == "TRIGGERED"
        and item.trigger is not None
        and item.stop is not None
        and item.trigger > item.stop
    )


def _blockers(evaluation: ShadowEvaluation) -> tuple[str, ...]:
    values = evaluation.payload.get("reason_codes", ())
    if not isinstance(values, (tuple, list)):
        return ()
    return tuple(dict.fromkeys(str(value) for value in values))


def _plan_trigger_crossed(plan: Mapping[str, object]) -> bool:
    return bool(
        plan.get("triggered_at_bar") is not None
        or str(plan.get("state")) in {
            "HIT_STOP", "REACHED_REWARD", "TRIGGERED_UNRESOLVED",
        }
    )


def _effective_reward_multiples(
    plans: Iterable[Mapping[str, object]],
) -> tuple[Decimal, ...]:
    """Honor the producer's conservative stop-first completed-bar convention."""

    values: list[Decimal] = []
    for plan in plans:
        stop_at = _optional_datetime(plan.get("stop_hit_at_bar"))
        rewards = plan.get("reward_hits", ())
        if not isinstance(rewards, (tuple, list)):
            continue
        for reward in rewards:
            if isinstance(reward, Mapping) and reward.get("multiple") is not None:
                reward_at = _optional_datetime(reward.get("bar_timestamp"))
                if stop_at is not None and (reward_at is None or reward_at >= stop_at):
                    continue
                values.append(Decimal(str(reward["multiple"])))
    return tuple(values)


def _decimal_values(
    outcomes: Iterable[ShadowOutcome], field: str,
) -> tuple[Decimal, ...]:
    return tuple(
        Decimal(str(item.payload[field])) for item in outcomes
        if item.payload.get(field) is not None
    )


def _strongest_classification(values: Iterable[str]) -> str:
    present = set(values)
    for value in (
        "DANGEROUS_MISSED_OPPORTUNITY", "MISSED_OPPORTUNITY",
        "MISSED_OPPORTUNITY_PRICE_MOVE_ONLY", "GOOD_REJECTION",
        "NEUTRAL_REJECTION", "UNAVAILABLE",
    ):
        if value in present:
            return value
    return "UNAVAILABLE"


def _catalyst_evidence_cohort(value: object) -> str:
    return {
        "TRUE": "CONFIRMED_CATALYST_PRESENT",
        "FALSE": "CONFIRMED_NO_CATALYST",
        "UNKNOWN": "CATALYST_UNKNOWN_OR_UNVERIFIED",
        "UNAVAILABLE": "CATALYST_EVIDENCE_UNAVAILABLE_CAUSE_UNSPECIFIED",
    }.get(str(value), "CATALYST_STATE_NOT_CAPTURED")


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[union-attr]
    raise TypeError(f"cannot encode {type(value).__name__}")


__all__ = [
    "CounterfactualEvaluationResult", "CounterfactualOutcome",
    "CounterfactualPolicy", "CounterfactualPolicySummary",
    "CounterfactualResearchReport", "CounterfactualStatus", "POLICIES",
    "ShadowCounterfactualAnalyzer", "counterfactual_report_as_json",
    "render_counterfactual_report",
]
