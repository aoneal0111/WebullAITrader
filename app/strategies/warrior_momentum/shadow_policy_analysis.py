"""Offline, non-executable policy analysis for Warrior shadow records.

The analyzer reads immutable capture records and never imports an order, broker,
gateway, authorization, or strategy runtime surface.  Opportunity grouping is
analytical attribution only; it is not an execution lifecycle identity.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from math import ceil
from pathlib import Path
import sqlite3
from statistics import mean, median
from typing import Callable, Iterable, Mapping, Sequence


DEFAULT_CAPTURE_PATH = Path(
    "data/warrior_momentum_v1_forward/forward_capture.sqlite3"
)
GROUPING_METHOD = "ANALYTICAL_GROUPING_SYMBOL_SESSION_GAP_V1"
COMPLETE = "COMPLETE"
UNAVAILABLE = "UNAVAILABLE"
TRUE_MISS = "MISSED_OPPORTUNITY"
PRICE_ONLY_MISS = "MISSED_OPPORTUNITY_PRICE_MOVE_ONLY"
DANGEROUS_MISS = "DANGEROUS_MISSED_OPPORTUNITY"


@dataclass(frozen=True, slots=True)
class ShadowPolicyAnalysisConfiguration:
    """Analysis settings which cannot configure production behavior."""

    opportunity_gap_minutes: int = 5
    percentile_minimum_sample: int = 10

    def __post_init__(self) -> None:
        if self.opportunity_gap_minutes <= 0 or self.percentile_minimum_sample <= 0:
            raise ValueError("policy analysis settings must be positive")


@dataclass(frozen=True, slots=True)
class ShadowEvaluation:
    record_id: str
    symbol: str
    timestamp: datetime
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.record_id or not self.symbol or self.timestamp.tzinfo is None:
            raise ValueError("shadow evaluation identity and aware timestamp are required")


@dataclass(frozen=True, slots=True)
class ShadowOutcome:
    record_id: str
    symbol: str
    timestamp: datetime
    evaluation_record_id: str
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if (
            not self.record_id
            or not self.symbol
            or not self.evaluation_record_id
            or self.timestamp.tzinfo is None
        ):
            raise ValueError("shadow outcome identity and aware timestamp are required")


@dataclass(frozen=True, slots=True)
class ShadowCaptureDataset:
    evaluations: tuple[ShadowEvaluation, ...]
    outcomes: tuple[ShadowOutcome, ...] = ()
    policy_result_count: int = 0
    data_cutoff: datetime | None = None


@dataclass(frozen=True, slots=True)
class AnalyticalOpportunity:
    opportunity_id: str
    symbol: str
    session: str
    first_timestamp: datetime
    last_timestamp: datetime
    evaluation_ids: tuple[str, ...]
    grouping_method: str = GROUPING_METHOD


@dataclass(frozen=True, slots=True)
class HorizonMetrics:
    complete_count: int
    median_return: Decimal | None
    mean_return: Decimal | None
    positive_return_rate: Decimal | None
    median_mfe: Decimal | None
    median_mae: Decimal | None
    percentile_90_mfe: Decimal | None
    percentile_90_mae: Decimal | None


@dataclass(frozen=True, slots=True)
class CohortMetrics:
    definition: str
    evaluation_count: int
    unique_opportunity_count: int
    outcome_count: int
    complete_horizon_count: int
    good_rejection_count: int
    neutral_rejection_count: int
    price_move_only_miss_count: int
    true_missed_opportunity_count: int
    dangerous_miss_count: int
    unavailable_count: int
    horizon_metrics: Mapping[int, HorizonMetrics]
    triggered_plan_count: int
    stop_hit_count: int
    one_r_reached_count: int
    two_r_reached_count: int
    triggered_unresolved_count: int
    true_missed_opportunity_rate: Decimal | None


@dataclass(frozen=True, slots=True)
class EvidenceRanking:
    name: str
    score: int
    recommendation: str
    true_miss_opportunities: int
    price_only_opportunities: int
    supporting_symbols: int
    supporting_sessions: int
    explanation: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TriggeredPlanCase:
    evaluation_id: str
    opportunity_id: str
    symbol: str
    timestamp: datetime
    session: str
    setup_type: str | None
    trigger: Decimal | None
    stop: Decimal | None
    blockers: tuple[str, ...]
    scanner_score: int | None
    scanner_status: str | None
    warrior_score: Decimal | None
    warrior_status: str | None
    horizons: Mapping[int, Mapping[str, object]]
    stop_hit: bool
    one_r_reached: bool
    two_r_reached: bool
    classification: str


@dataclass(frozen=True, slots=True)
class NearEligibleCase:
    evaluation_id: str
    opportunity_id: str
    symbol: str
    timestamp: datetime
    session: str
    blockers: tuple[str, ...]
    scanner_status: str | None
    warrior_status: str | None
    setup_state: str | None
    setup_type: str | None
    trigger: Decimal | None
    stop: Decimal | None
    strongest_classification: str
    maximum_mfe: Decimal | None
    minimum_mae: Decimal | None
    one_r_reached: bool
    two_r_reached: bool
    evidence_strength: int


@dataclass(frozen=True, slots=True)
class NoSetupAnalysis:
    evaluation_count: int
    unique_symbol_count: int
    unique_opportunity_count: int
    repeated_evaluation_count: int
    symbols_later_forming_or_triggered_count: int
    symbols_later_triggered_count: int
    symbols_never_produced_setup_count: int
    symbol_later_forming_or_triggered_rate: Decimal | None
    symbol_later_triggered_rate: Decimal | None
    symbol_never_produced_setup_rate: Decimal | None
    later_forming_or_triggered_count: int
    later_triggered_count: int
    never_produced_setup_count: int
    later_forming_or_triggered_rate: Decimal | None
    later_triggered_rate: Decimal | None
    never_produced_setup_rate: Decimal | None
    median_minutes_to_later_setup: Decimal | None
    price_move_only_symbol_count: int
    true_miss_symbol_count: int
    price_move_only_symbol_rate: Decimal | None
    true_miss_symbol_rate: Decimal | None
    price_move_only_opportunity_rate: Decimal | None
    true_miss_opportunity_rate: Decimal | None


@dataclass(frozen=True, slots=True)
class ShadowPolicyReport:
    generated_at: datetime
    data_cutoff: datetime | None
    grouping_method: str
    evaluation_count: int
    outcome_count: int
    policy_result_count: int
    opportunities: tuple[AnalyticalOpportunity, ...]
    overall: CohortMetrics
    blockers: Mapping[str, CohortMetrics]
    blocker_cohorts: Mapping[str, Mapping[str, CohortMetrics]]
    exact_combinations: Mapping[str, CohortMetrics]
    setup_cohorts: Mapping[str, CohortMetrics]
    session_cohorts: Mapping[str, CohortMetrics]
    scanner_cohorts: Mapping[str, CohortMetrics]
    warrior_cohorts: Mapping[str, CohortMetrics]
    no_setup: NoSetupAnalysis
    triggered_plans: tuple[TriggeredPlanCase, ...]
    near_eligible: tuple[NearEligibleCase, ...]
    evidence_ranking: tuple[EvidenceRanking, ...]


def load_shadow_dataset_read_only(path: Path = DEFAULT_CAPTURE_PATH) -> ShadowCaptureDataset:
    """Load only shadow records using SQLite's read-only and query-only modes."""

    resolved = path.resolve(strict=True)
    connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        evaluations = tuple(
            ShadowEvaluation(
                str(record_id), str(symbol), _aware_datetime(timestamp),
                _object_payload(payload_json),
            )
            for record_id, symbol, timestamp, payload_json in connection.execute(
                "SELECT record_id, symbol, timestamp, payload_json "
                "FROM capture_records WHERE record_type = 'SHADOW_EVALUATION' "
                "ORDER BY timestamp, sequence"
            )
        )
        outcomes = tuple(
            ShadowOutcome(
                str(record_id), str(symbol), _aware_datetime(timestamp),
                str(payload["evaluation_record_id"]), payload,
            )
            for record_id, symbol, timestamp, payload_json in connection.execute(
                "SELECT record_id, symbol, timestamp, payload_json "
                "FROM capture_records WHERE record_type = 'SHADOW_OUTCOME' "
                "ORDER BY timestamp, sequence"
            )
            for payload in (_object_payload(payload_json),)
        )
        policy_count = int(connection.execute(
            "SELECT COUNT(*) FROM capture_records "
            "WHERE record_type = 'SHADOW_POLICY_RESULT'"
        ).fetchone()[0])
    finally:
        connection.close()
    timestamps = tuple(item.timestamp for item in (*evaluations, *outcomes))
    return ShadowCaptureDataset(
        evaluations, outcomes, policy_count, max(timestamps, default=None),
    )


class ShadowPolicyAnalyzer:
    """Aggregate shadow evidence without any production runtime dependency."""

    def __init__(
        self,
        config: ShadowPolicyAnalysisConfiguration = ShadowPolicyAnalysisConfiguration(),
    ) -> None:
        self.config = config

    def analyze(
        self, dataset: ShadowCaptureDataset, *, generated_at: datetime | None = None,
    ) -> ShadowPolicyReport:
        evaluations = tuple(sorted(dataset.evaluations, key=lambda item: item.timestamp))
        evaluation_by_id = {item.record_id: item for item in evaluations}
        outcomes = tuple(
            item for item in dataset.outcomes if item.evaluation_record_id in evaluation_by_id
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
        all_ids = frozenset(evaluation_by_id)

        def metrics(ids: Iterable[str], definition: str) -> CohortMetrics:
            return _cohort_metrics(
                frozenset(ids), definition, outcomes_by_evaluation,
                opportunity_by_evaluation, evaluation_by_id, self.config,
            )

        blocker_names = sorted({
            blocker for item in evaluations for blocker in _blockers(item)
        })
        blockers = {
            blocker: metrics(
                (item.record_id for item in evaluations if blocker in _blockers(item)),
                f"evaluations containing {blocker}",
            )
            for blocker in blocker_names
        }
        blocker_cohorts = {
            blocker: self._blocker_cohorts(blocker, evaluations, metrics)
            for blocker in blocker_names
        }
        combinations: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for item in evaluations:
            combinations[_blockers(item)].append(item.record_id)
        exact_combinations = {
            " + ".join(combo) if combo else "UNATTRIBUTED": metrics(
                ids, f"exact blocker set: {combo or ('UNATTRIBUTED',)}",
            )
            for combo, ids in sorted(combinations.items())
        }
        setup_cohorts = _categorical_cohorts(
            evaluations, metrics, "setup_state", "NO_SETUP_STATE",
        )
        session_cohorts = _categorical_cohorts(
            evaluations, metrics, "session", "UNKNOWN_SESSION",
        )
        scanner_cohorts = _categorical_cohorts(
            evaluations, metrics, "scanner_classification", "UNCLASSIFIED",
        )
        warrior_cohorts = _categorical_cohorts(
            evaluations, metrics, "warrior_status", "UNKNOWN_STATUS",
        )
        triggered = _triggered_cases(
            evaluations, outcomes_by_evaluation, opportunity_by_evaluation,
        )
        near_eligible = _near_eligible_cases(
            evaluations, outcomes_by_evaluation, opportunity_by_evaluation,
        )
        no_setup = _no_setup_analysis(
            evaluations, outcomes_by_evaluation, opportunities,
        )
        ranking = tuple(sorted(
            (
                _rank_blocker(
                    blocker, evaluations, outcomes_by_evaluation,
                    opportunity_by_evaluation,
                )
                for blocker in blocker_names
            ),
            key=lambda item: (-item.score, item.name),
        ))
        report_time = generated_at or dataset.data_cutoff or datetime.now().astimezone()
        if report_time.tzinfo is None:
            raise ValueError("report generation timestamp must be timezone-aware")
        return ShadowPolicyReport(
            report_time, dataset.data_cutoff, GROUPING_METHOD,
            len(evaluations), len(outcomes), dataset.policy_result_count,
            opportunities, metrics(all_ids, "all shadow evaluations"),
            blockers, blocker_cohorts, exact_combinations, setup_cohorts,
            session_cohorts, scanner_cohorts, warrior_cohorts, no_setup,
            triggered, near_eligible, ranking,
        )

    def _blocker_cohorts(
        self,
        blocker: str,
        evaluations: Sequence[ShadowEvaluation],
        metrics: Callable[[Iterable[str], str], CohortMetrics],
    ) -> Mapping[str, CohortMetrics]:
        selected = tuple(item for item in evaluations if blocker in _blockers(item))
        cohort_filters = {
            "ONLY": lambda item: _blockers(item) == (blocker,),
            "WITH_NO_SETUP": lambda item: "NO_SETUP" in _blockers(item),
            "WITH_RISK_REJECTED": lambda item: "RISK_REJECTED" in _blockers(item),
            "WITHOUT_NO_SETUP": lambda item: "NO_SETUP" not in _blockers(item),
            "AUTHORITATIVE_TRIGGERED_PLAN": _has_authoritative_triggered_plan,
            "SCANNER_QUALIFYING": lambda item: (
                str(item.payload.get("scanner_classification")) == "QUALIFYING"
            ),
            "WARRIOR_QUALIFIED": lambda item: (
                str(item.payload.get("warrior_status")) == "QUALIFIED"
            ),
        }
        for session in ("REGULAR", "PREMARKET", "AFTER_HOURS"):
            cohort_filters[session] = (
                lambda item, expected=session: str(item.payload.get("session")) == expected
            )
        result: dict[str, CohortMetrics] = {}
        for name, predicate in cohort_filters.items():
            ids = (item.record_id for item in selected if predicate(item))
            result[name] = metrics(ids, f"{blocker}: {name}")
        return result


def group_analytical_opportunities(
    evaluations: Iterable[ShadowEvaluation],
    config: ShadowPolicyAnalysisConfiguration = ShadowPolicyAnalysisConfiguration(),
) -> tuple[AnalyticalOpportunity, ...]:
    """Group same-symbol observations separated by no more than a bounded gap."""

    grouped: dict[tuple[str, str], list[ShadowEvaluation]] = defaultdict(list)
    for item in evaluations:
        grouped[(item.symbol.upper(), str(item.payload.get("session") or "UNKNOWN"))].append(item)
    result: list[AnalyticalOpportunity] = []
    maximum_gap = timedelta(minutes=config.opportunity_gap_minutes)
    for (symbol, session), items in sorted(grouped.items()):
        current: list[ShadowEvaluation] = []
        for item in sorted(items, key=lambda value: value.timestamp):
            if current and item.timestamp - current[-1].timestamp > maximum_gap:
                result.append(_opportunity(symbol, session, current))
                current = []
            current.append(item)
        if current:
            result.append(_opportunity(symbol, session, current))
    return tuple(sorted(result, key=lambda item: (item.first_timestamp, item.symbol)))


def _opportunity(
    symbol: str, session: str, evaluations: Sequence[ShadowEvaluation],
) -> AnalyticalOpportunity:
    identity = "|".join((
        GROUPING_METHOD, symbol, session, evaluations[0].timestamp.isoformat(),
        evaluations[-1].timestamp.isoformat(),
        *(item.record_id for item in evaluations),
    ))
    return AnalyticalOpportunity(
        sha256(identity.encode("utf-8")).hexdigest(), symbol, session,
        evaluations[0].timestamp, evaluations[-1].timestamp,
        tuple(item.record_id for item in evaluations),
    )


def _cohort_metrics(
    evaluation_ids: frozenset[str],
    definition: str,
    outcomes_by_evaluation: Mapping[str, Sequence[ShadowOutcome]],
    opportunity_by_evaluation: Mapping[str, AnalyticalOpportunity],
    evaluation_by_id: Mapping[str, ShadowEvaluation],
    config: ShadowPolicyAnalysisConfiguration,
) -> CohortMetrics:
    outcomes = tuple(
        outcome
        for evaluation_id in evaluation_ids
        for outcome in outcomes_by_evaluation.get(evaluation_id, ())
    )
    complete = tuple(item for item in outcomes if item.payload.get("status") == COMPLETE)
    classifications = Counter(str(item.payload.get("classification")) for item in outcomes)
    horizons = sorted({int(item.payload["horizon_minutes"]) for item in outcomes})
    horizon_metrics = {
        horizon: _horizon_metrics(
            tuple(
                item for item in complete
                if int(item.payload["horizon_minutes"]) == horizon
            ),
            config.percentile_minimum_sample,
        )
        for horizon in horizons
    }
    plan_summary = _plan_summary(
        evaluation_ids, evaluation_by_id, outcomes_by_evaluation,
    )
    true_miss_evaluations = {
        item.evaluation_record_id
        for item in complete if item.payload.get("classification") == TRUE_MISS
    }
    triggered_count = plan_summary["triggered_plan_count"]
    return CohortMetrics(
        definition, len(evaluation_ids),
        len({opportunity_by_evaluation[item].opportunity_id for item in evaluation_ids}),
        len(outcomes), len(complete),
        classifications["GOOD_REJECTION"], classifications["NEUTRAL_REJECTION"],
        classifications[PRICE_ONLY_MISS], classifications[TRUE_MISS],
        classifications[DANGEROUS_MISS], classifications[UNAVAILABLE],
        horizon_metrics, triggered_count, plan_summary["stop_hit_count"],
        plan_summary["one_r_reached_count"], plan_summary["two_r_reached_count"],
        plan_summary["triggered_unresolved_count"],
        _rate(len(true_miss_evaluations), triggered_count),
    )


def _horizon_metrics(
    outcomes: Sequence[ShadowOutcome], percentile_minimum: int,
) -> HorizonMetrics:
    returns = _decimal_values(outcomes, "return_percent")
    mfe = _decimal_values(outcomes, "mfe_percent")
    mae = _decimal_values(outcomes, "mae_percent")
    return HorizonMetrics(
        len(outcomes), _median(returns), _mean(returns),
        _rate(sum(value > 0 for value in returns), len(returns)),
        _median(mfe), _median(mae),
        _percentile_90(mfe, percentile_minimum),
        _percentile_90(mae, percentile_minimum),
    )


def _plan_summary(
    evaluation_ids: Iterable[str],
    evaluation_by_id: Mapping[str, ShadowEvaluation],
    outcomes_by_evaluation: Mapping[str, Sequence[ShadowOutcome]],
) -> dict[str, int]:
    counters = Counter()
    for evaluation_id in evaluation_ids:
        evaluation = evaluation_by_id[evaluation_id]
        if not _has_authoritative_triggered_plan(evaluation):
            continue
        counters["triggered_plan_count"] += 1
        complete = tuple(
            item for item in outcomes_by_evaluation.get(evaluation_id, ())
            if item.payload.get("status") == COMPLETE
        )
        plans = tuple(
            item.payload.get("hypothetical_trade") for item in complete
            if isinstance(item.payload.get("hypothetical_trade"), Mapping)
            and bool(item.payload["hypothetical_trade"].get("applicable"))  # type: ignore[index]
        )
        if not plans:
            counters["triggered_unresolved_count"] += 1
            continue
        states = {str(plan.get("state")) for plan in plans}  # type: ignore[union-attr]
        if any(plan.get("stop_hit_at_bar") is not None for plan in plans):  # type: ignore[union-attr]
            counters["stop_hit_count"] += 1
        multiples = _reward_multiples(plans)
        if any(value >= 1 for value in multiples):
            counters["one_r_reached_count"] += 1
        if any(value >= 2 for value in multiples):
            counters["two_r_reached_count"] += 1
        if "TRIGGERED_UNRESOLVED" in states:
            counters["triggered_unresolved_count"] += 1
    return {
        name: counters[name] for name in (
            "triggered_plan_count", "stop_hit_count", "one_r_reached_count",
            "two_r_reached_count", "triggered_unresolved_count",
        )
    }


def _categorical_cohorts(
    evaluations: Sequence[ShadowEvaluation],
    metrics: Callable[[Iterable[str], str], CohortMetrics],
    field: str, missing: str,
) -> Mapping[str, CohortMetrics]:
    groups: dict[str, list[str]] = defaultdict(list)
    for item in evaluations:
        value = item.payload.get(field)
        groups[str(value) if value is not None else missing].append(item.record_id)
    return {
        name: metrics(ids, f"{field} = {name}")
        for name, ids in sorted(groups.items())
    }


def _triggered_cases(
    evaluations: Sequence[ShadowEvaluation],
    outcomes_by_evaluation: Mapping[str, Sequence[ShadowOutcome]],
    opportunity_by_evaluation: Mapping[str, AnalyticalOpportunity],
) -> tuple[TriggeredPlanCase, ...]:
    result = []
    for item in evaluations:
        if not _has_authoritative_triggered_plan(item):
            continue
        outcomes = tuple(sorted(
            outcomes_by_evaluation.get(item.record_id, ()),
            key=lambda value: int(value.payload["horizon_minutes"]),
        ))
        plans = tuple(
            outcome.payload.get("hypothetical_trade") for outcome in outcomes
            if isinstance(outcome.payload.get("hypothetical_trade"), Mapping)
        )
        multiples = _reward_multiples(plans)
        classifications = tuple(str(value.payload.get("classification")) for value in outcomes)
        result.append(TriggeredPlanCase(
            item.record_id, opportunity_by_evaluation[item.record_id].opportunity_id,
            item.symbol, item.timestamp, str(item.payload.get("session")),
            _optional_string(item.payload.get("setup_type")),
            _optional_decimal(item.payload.get("trigger")),
            _optional_decimal(item.payload.get("stop")), _blockers(item),
            _optional_int(item.payload.get("scanner_score")),
            _optional_string(item.payload.get("scanner_classification")),
            _optional_decimal(item.payload.get("warrior_score")),
            _optional_string(item.payload.get("warrior_status")),
            {
                int(value.payload["horizon_minutes"]): {
                    key: value.payload.get(key) for key in (
                        "status", "classification", "return_percent",
                        "mfe_percent", "mae_percent",
                    )
                }
                for value in outcomes
            },
            any(plan.get("stop_hit_at_bar") is not None for plan in plans),
            any(value >= 1 for value in multiples),
            any(value >= 2 for value in multiples),
            _strongest_classification(classifications),
        ))
    return tuple(result)


def _near_eligible_cases(
    evaluations: Sequence[ShadowEvaluation],
    outcomes_by_evaluation: Mapping[str, Sequence[ShadowOutcome]],
    opportunity_by_evaluation: Mapping[str, AnalyticalOpportunity],
) -> tuple[NearEligibleCase, ...]:
    result = []
    for item in evaluations:
        blockers = _blockers(item)
        if len(blockers) not in (1, 2):
            continue
        complete = tuple(
            value for value in outcomes_by_evaluation.get(item.record_id, ())
            if value.payload.get("status") == COMPLETE
        )
        classifications = tuple(str(value.payload.get("classification")) for value in complete)
        plans = tuple(
            value.payload.get("hypothetical_trade") for value in complete
            if isinstance(value.payload.get("hypothetical_trade"), Mapping)
        )
        multiples = _reward_multiples(plans)
        strongest = _strongest_classification(classifications)
        strength = (
            (40 if strongest == TRUE_MISS else 0)
            + (15 if _has_authoritative_triggered_plan(item) else 0)
            + (10 if len(blockers) == 1 else 5)
            + (8 if any(value >= 1 for value in multiples) else 0)
            + (8 if any(value >= 2 for value in multiples) else 0)
            + (2 if strongest == PRICE_ONLY_MISS else 0)
            - (5 if not complete else 0)
        )
        result.append(NearEligibleCase(
            item.record_id, opportunity_by_evaluation[item.record_id].opportunity_id,
            item.symbol, item.timestamp, str(item.payload.get("session")), blockers,
            _optional_string(item.payload.get("scanner_classification")),
            _optional_string(item.payload.get("warrior_status")),
            _optional_string(item.payload.get("setup_state")),
            _optional_string(item.payload.get("setup_type")),
            _optional_decimal(item.payload.get("trigger")),
            _optional_decimal(item.payload.get("stop")), strongest,
            max(_decimal_values(complete, "mfe_percent"), default=None),
            min(_decimal_values(complete, "mae_percent"), default=None),
            any(value >= 1 for value in multiples),
            any(value >= 2 for value in multiples), strength,
        ))
    return tuple(sorted(
        result, key=lambda value: (-value.evidence_strength, value.timestamp),
    ))


def _no_setup_analysis(
    evaluations: Sequence[ShadowEvaluation],
    outcomes_by_evaluation: Mapping[str, Sequence[ShadowOutcome]],
    opportunities: Sequence[AnalyticalOpportunity],
) -> NoSetupAnalysis:
    by_id = {item.record_id: item for item in evaluations}
    relevant = tuple(
        opportunity for opportunity in opportunities
        if any("NO_SETUP" in _blockers(by_id[item]) for item in opportunity.evaluation_ids)
    )
    forming_or_triggered = 0
    triggered = 0
    times: list[Decimal] = []
    price_only = 0
    true_miss = 0
    price_only_symbols: set[str] = set()
    true_miss_symbols: set[str] = set()
    raw_count = 0
    symbols: set[str] = set()
    for opportunity in relevant:
        items = tuple(by_id[item] for item in opportunity.evaluation_ids)
        no_setup_items = tuple(item for item in items if "NO_SETUP" in _blockers(item))
        raw_count += len(no_setup_items)
        symbols.add(opportunity.symbol)
        first_no_setup = min(item.timestamp for item in no_setup_items)
        later_setup = tuple(
            item for item in items
            if item.timestamp > first_no_setup
            and str(item.payload.get("setup_state")) in {"FORMING", "TRIGGERED"}
        )
        later_triggered = tuple(
            item for item in later_setup if item.payload.get("setup_state") == "TRIGGERED"
        )
        if later_setup:
            forming_or_triggered += 1
            elapsed = min(item.timestamp for item in later_setup) - first_no_setup
            times.append(Decimal(str(elapsed.total_seconds())) / Decimal("60"))
        if later_triggered:
            triggered += 1
        classifications = {
            str(outcome.payload.get("classification"))
            for item in items
            for outcome in outcomes_by_evaluation.get(item.record_id, ())
            if outcome.payload.get("status") == COMPLETE
        }
        price_only += PRICE_ONLY_MISS in classifications
        true_miss += TRUE_MISS in classifications
        if PRICE_ONLY_MISS in classifications:
            price_only_symbols.add(opportunity.symbol)
        if TRUE_MISS in classifications:
            true_miss_symbols.add(opportunity.symbol)
    total = len(relevant)
    later_setup_symbols: set[str] = set()
    later_triggered_symbols: set[str] = set()
    for opportunity in relevant:
        items = tuple(by_id[item] for item in opportunity.evaluation_ids)
        first_no_setup = min(
            item.timestamp for item in items if "NO_SETUP" in _blockers(item)
        )
        if any(
            item.timestamp > first_no_setup
            and str(item.payload.get("setup_state")) in {"FORMING", "TRIGGERED"}
            for item in items
        ):
            later_setup_symbols.add(opportunity.symbol)
        if any(
            item.timestamp > first_no_setup
            and item.payload.get("setup_state") == "TRIGGERED"
            for item in items
        ):
            later_triggered_symbols.add(opportunity.symbol)
    symbol_total = len(symbols)
    return NoSetupAnalysis(
        evaluation_count=raw_count,
        unique_symbol_count=symbol_total,
        unique_opportunity_count=total,
        repeated_evaluation_count=raw_count - total,
        symbols_later_forming_or_triggered_count=len(later_setup_symbols),
        symbols_later_triggered_count=len(later_triggered_symbols),
        symbols_never_produced_setup_count=symbol_total - len(later_setup_symbols),
        symbol_later_forming_or_triggered_rate=_rate(
            len(later_setup_symbols), symbol_total,
        ),
        symbol_later_triggered_rate=_rate(len(later_triggered_symbols), symbol_total),
        symbol_never_produced_setup_rate=_rate(
            symbol_total - len(later_setup_symbols), symbol_total,
        ),
        later_forming_or_triggered_count=forming_or_triggered,
        later_triggered_count=triggered,
        never_produced_setup_count=total - forming_or_triggered,
        later_forming_or_triggered_rate=_rate(forming_or_triggered, total),
        later_triggered_rate=_rate(triggered, total),
        never_produced_setup_rate=_rate(total - forming_or_triggered, total),
        median_minutes_to_later_setup=_median(times),
        price_move_only_symbol_count=len(price_only_symbols),
        true_miss_symbol_count=len(true_miss_symbols),
        price_move_only_symbol_rate=_rate(len(price_only_symbols), symbol_total),
        true_miss_symbol_rate=_rate(len(true_miss_symbols), symbol_total),
        price_move_only_opportunity_rate=_rate(price_only, total),
        true_miss_opportunity_rate=_rate(true_miss, total),
    )


def _rank_blocker(
    blocker: str,
    evaluations: Sequence[ShadowEvaluation],
    outcomes_by_evaluation: Mapping[str, Sequence[ShadowOutcome]],
    opportunity_by_evaluation: Mapping[str, AnalyticalOpportunity],
) -> EvidenceRanking:
    selected = tuple(item for item in evaluations if blocker in _blockers(item))
    true_items = tuple(
        item for item in selected
        if any(
            outcome.payload.get("status") == COMPLETE
            and outcome.payload.get("classification") == TRUE_MISS
            for outcome in outcomes_by_evaluation.get(item.record_id, ())
        )
    )
    price_items = tuple(
        item for item in selected
        if any(
            outcome.payload.get("status") == COMPLETE
            and outcome.payload.get("classification") == PRICE_ONLY_MISS
            for outcome in outcomes_by_evaluation.get(item.record_id, ())
        )
    )
    true_opportunities = {
        opportunity_by_evaluation[item.record_id].opportunity_id for item in true_items
    }
    price_opportunities = {
        opportunity_by_evaluation[item.record_id].opportunity_id for item in price_items
    }
    supporting_symbols = {item.symbol for item in true_items}
    supporting_sessions = {str(item.payload.get("session")) for item in true_items}
    one_r_opportunities: set[str] = set()
    two_r_opportunities: set[str] = set()
    for item in true_items:
        plans = tuple(
            outcome.payload.get("hypothetical_trade")
            for outcome in outcomes_by_evaluation.get(item.record_id, ())
            if isinstance(outcome.payload.get("hypothetical_trade"), Mapping)
        )
        multiples = _reward_multiples(plans)
        opportunity_id = opportunity_by_evaluation[item.record_id].opportunity_id
        if any(value >= 1 for value in multiples):
            one_r_opportunities.add(opportunity_id)
        if any(value >= 2 for value in multiples):
            two_r_opportunities.add(opportunity_id)
    minimal_true = {
        opportunity_by_evaluation[item.record_id].opportunity_id
        for item in true_items if len(_blockers(item)) <= 2
    }
    score = (
        30 * len(true_opportunities)
        + 8 * len(one_r_opportunities)
        + 12 * len(two_r_opportunities)
        + 10 * len(minimal_true)
        + min(10, 2 * len(supporting_symbols))
        + min(6, 2 * len(supporting_sessions))
    )
    explanation: list[str] = []
    if true_opportunities:
        explanation.append(
            f"{len(true_opportunities)} unique opportunity with a true miss"
            if len(true_opportunities) == 1 else
            f"{len(true_opportunities)} unique opportunities with true misses"
        )
    if one_r_opportunities:
        explanation.append(f"{len(one_r_opportunities)} unique opportunity reached 1R")
    if not minimal_true and true_opportunities:
        score -= 18
        explanation.append("true-miss evidence is confounded by more than two blockers")
    if len(supporting_symbols) < 2 and true_opportunities:
        score -= 10
        explanation.append("true-miss evidence is limited to one symbol")
    if len(supporting_sessions) < 2 and true_opportunities:
        score -= 4
        explanation.append("true-miss evidence is limited to one session cohort")
    if len(true_opportunities) == 1:
        score -= 8
        explanation.append("sample contains only one independent opportunity")
    if price_opportunities and not true_opportunities:
        explanation.append(
            f"{len(price_opportunities)} price-move-only opportunities provide weak evidence"
        )
    score = max(0, score)
    complete = sum(
        outcome.payload.get("status") == COMPLETE
        for item in selected for outcome in outcomes_by_evaluation.get(item.record_id, ())
    )
    good = sum(
        outcome.payload.get("classification") == "GOOD_REJECTION"
        for item in selected for outcome in outcomes_by_evaluation.get(item.record_id, ())
    )
    if score >= 80:
        recommendation = "STRONG_INVESTIGATION_CANDIDATE"
    elif score >= 40:
        recommendation = "INVESTIGATE"
    elif not true_opportunities and complete >= 10 and good > len(price_opportunities):
        recommendation = "KEEP_STRICT"
    else:
        recommendation = "INSUFFICIENT_EVIDENCE"
    if not explanation:
        explanation.append("no true-miss or price-move-only opportunity evidence")
    return EvidenceRanking(
        blocker, score, recommendation, len(true_opportunities),
        len(price_opportunities), len(supporting_symbols), len(supporting_sessions),
        tuple(explanation),
    )


def render_policy_report(report: ShadowPolicyReport) -> str:
    """Render a concise, deterministic human-readable evidence report."""

    lines = [
        "ATLAS SHADOW REJECTION POLICY ANALYSIS",
        f"data cutoff: {_display(report.data_cutoff)}",
        f"raw evaluations: {report.evaluation_count}",
        f"outcomes: {report.outcome_count}",
        f"unique analytical opportunities: {len(report.opportunities)}",
        f"grouping: {report.grouping_method}",
        "",
        "BLOCKER METRICS",
    ]
    for name, item in report.blockers.items():
        lines.append(
            f"{name}: evaluations={item.evaluation_count} "
            f"opportunities={item.unique_opportunity_count} "
            f"complete={item.complete_horizon_count} good={item.good_rejection_count} "
            f"neutral={item.neutral_rejection_count} "
            f"price-only={item.price_move_only_miss_count} "
            f"true-miss={item.true_missed_opportunity_count} "
            f"dangerous={item.dangerous_miss_count} unavailable={item.unavailable_count} "
            f"plans={item.triggered_plan_count} stop={item.stop_hit_count} "
            f"1R={item.one_r_reached_count} 2R={item.two_r_reached_count}"
        )
        for horizon, values in sorted(item.horizon_metrics.items()):
            lines.append(
                f"  +{horizon}m n={values.complete_count} "
                f"median/mean return={_display(values.median_return)}/"
                f"{_display(values.mean_return)} positive={_display(values.positive_return_rate)} "
                f"median MFE/MAE={_display(values.median_mfe)}/"
                f"{_display(values.median_mae)} p90 MFE/MAE="
                f"{_display(values.percentile_90_mfe)}/"
                f"{_display(values.percentile_90_mae)}"
            )
    lines.extend((
        "",
        "EVIDENCE RANKING",
    ))
    for item in report.evidence_ranking:
        lines.append(
            f"{item.name}: score={item.score} recommendation={item.recommendation}; "
            + "; ".join(item.explanation)
        )
    lines.extend(("", "TRIGGERED PLANS"))
    for item in report.triggered_plans:
        horizons = ", ".join(
            f"+{h}m={value.get('status')}/{value.get('classification')}"
            for h, value in sorted(item.horizons.items())
        ) or "no outcomes"
        lines.append(
            f"{item.symbol} {item.timestamp.isoformat()} blockers={'+'.join(item.blockers)} "
            f"setup={item.setup_type} trigger={item.trigger} stop={item.stop} "
            f"1R={item.one_r_reached} 2R={item.two_r_reached} "
            f"classification={item.classification}; {horizons}"
        )
    lines.extend(("", "NEAR-ELIGIBLE (ONE OR TWO BLOCKERS)"))
    for item in report.near_eligible:
        lines.append(
            f"{item.symbol} {item.timestamp.isoformat()} session={item.session} "
            f"blockers={'+'.join(item.blockers)} scanner={item.scanner_status} "
            f"warrior={item.warrior_status} setup={item.setup_state}/{item.setup_type} "
            f"trigger={item.trigger} stop={item.stop} "
            f"classification={item.strongest_classification} "
            f"MFE/MAE={_display(item.maximum_mfe)}/{_display(item.minimum_mae)} "
            f"1R={item.one_r_reached} 2R={item.two_r_reached} "
            f"strength={item.evidence_strength}"
        )
    no_setup = report.no_setup
    lines.extend((
        "", "NO_SETUP",
        f"raw={no_setup.evaluation_count} opportunities={no_setup.unique_opportunity_count} "
        f"symbols={no_setup.unique_symbol_count} repeated={no_setup.repeated_evaluation_count}",
        f"symbols later forming/triggered="
        f"{no_setup.symbols_later_forming_or_triggered_count} "
        f"({_display(no_setup.symbol_later_forming_or_triggered_rate)}); "
        f"symbols later triggered={no_setup.symbols_later_triggered_count} "
        f"({_display(no_setup.symbol_later_triggered_rate)})",
        f"later forming/triggered={no_setup.later_forming_or_triggered_count} "
        f"({_display(no_setup.later_forming_or_triggered_rate)}); "
        f"later triggered={no_setup.later_triggered_count} "
        f"({_display(no_setup.later_triggered_rate)})",
        f"price-move-only opportunity rate={_display(no_setup.price_move_only_opportunity_rate)}; "
        f"true-miss opportunity rate={_display(no_setup.true_miss_opportunity_rate)}",
        f"price-move-only symbol rate={_display(no_setup.price_move_only_symbol_rate)}; "
        f"true-miss symbol rate={_display(no_setup.true_miss_symbol_rate)}",
        "", "SHADOW_POLICY_RESULT",
        f"existing persisted records={report.policy_result_count}; "
        "this read-only analysis does not persist new records",
    ))
    return "\n".join(lines)


def report_as_json(report: ShadowPolicyReport) -> str:
    return json.dumps(asdict(report), default=_json_default, sort_keys=True, indent=2)


def _blockers(item: ShadowEvaluation) -> tuple[str, ...]:
    values = item.payload.get("reason_codes", ())
    if not isinstance(values, (tuple, list)):
        return ()
    return tuple(dict.fromkeys(str(value) for value in values))


def _has_authoritative_triggered_plan(item: ShadowEvaluation) -> bool:
    trigger = _optional_decimal(item.payload.get("trigger"))
    stop = _optional_decimal(item.payload.get("stop"))
    return bool(
        item.payload.get("counterfactual_entry_valid")
        and item.payload.get("setup_state") == "TRIGGERED"
        and trigger is not None and stop is not None and trigger > stop
    )


def _reward_multiples(plans: Iterable[object]) -> tuple[Decimal, ...]:
    result = []
    for plan in plans:
        if not isinstance(plan, Mapping):
            continue
        rewards = plan.get("reward_hits", ())
        if not isinstance(rewards, (tuple, list)):
            continue
        for reward in rewards:
            if isinstance(reward, Mapping) and reward.get("multiple") is not None:
                result.append(Decimal(str(reward["multiple"])))
    return tuple(result)


def _strongest_classification(values: Iterable[str]) -> str:
    present = set(values)
    for value in (DANGEROUS_MISS, TRUE_MISS, PRICE_ONLY_MISS,
                  "GOOD_REJECTION", "NEUTRAL_REJECTION", UNAVAILABLE):
        if value in present:
            return value
    return UNAVAILABLE


def _decimal_values(
    outcomes: Iterable[ShadowOutcome], field: str,
) -> tuple[Decimal, ...]:
    return tuple(
        Decimal(str(value.payload[field])) for value in outcomes
        if value.payload.get(field) is not None
    )


def _rate(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return Decimal(numerator) / Decimal(denominator)


def _median(values: Sequence[Decimal]) -> Decimal | None:
    return median(values) if values else None


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    return mean(values) if values else None


def _percentile_90(
    values: Sequence[Decimal], minimum_sample: int,
) -> Decimal | None:
    if len(values) < minimum_sample:
        return None
    ordered = sorted(values)
    return ordered[ceil(Decimal("0.9") * len(ordered)) - 1]


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)


def _aware_datetime(value: object) -> datetime:
    result = datetime.fromisoformat(str(value))
    if result.tzinfo is None:
        raise ValueError("captured timestamps must be timezone-aware")
    return result


def _object_payload(value: object) -> dict[str, object]:
    result = json.loads(str(value))
    if not isinstance(result, dict):
        raise ValueError("capture payload must be an object")
    return result


def _display(value: object) -> str:
    return "N/A" if value is None else str(value)


def _json_default(value: object) -> object:
    if isinstance(value, (datetime, Decimal)):
        return str(value) if isinstance(value, Decimal) else value.isoformat()
    raise TypeError(f"cannot encode {type(value).__name__}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_CAPTURE_PATH)
    parser.add_argument("--json", action="store_true", dest="as_json")
    arguments = parser.parse_args(argv)
    dataset = load_shadow_dataset_read_only(arguments.database)
    report = ShadowPolicyAnalyzer().analyze(dataset)
    print(report_as_json(report) if arguments.as_json else render_policy_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AnalyticalOpportunity", "CohortMetrics", "EvidenceRanking",
    "HorizonMetrics", "NearEligibleCase", "NoSetupAnalysis",
    "ShadowCaptureDataset", "ShadowEvaluation", "ShadowOutcome",
    "ShadowPolicyAnalysisConfiguration", "ShadowPolicyAnalyzer",
    "ShadowPolicyReport", "TriggeredPlanCase", "group_analytical_opportunities",
    "load_shadow_dataset_read_only", "render_policy_report", "report_as_json",
]
