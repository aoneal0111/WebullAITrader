"""Leakage-free learning examples derived from immutable Trade Intelligence facts."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from zoneinfo import ZoneInfo

from app.trade_intelligence.models import (
    AtlasDecision, DatasetPartition, DecisionObservation, HorizonOutcome,
    OutcomeStatus, ResearchGeneration, TradeOpportunityExperience, canonical_json,
)

from .contracts import (
    LearningExample, LearningFeatureVector, LearningLabels, LearningTarget,
    ResearchEvidencePolicy, SufficiencyAssessment, EvidenceStatus,
)

CHAMPION_SELECTIONS = frozenset({
    AtlasDecision.ENTRY_READY, AtlasDecision.ORDER_SUBMITTED, AtlasDecision.FILLED,
})

BASE_FIELDS = (
    "last_price", "percentage_change", "spread_percent", "dollar_volume",
    "relative_volume", "float_shares", "tradable", "halted",
    "quote_freshness_seconds", "trade_freshness_seconds", "catalyst_status",
    "catalyst_type", "scanner_qualified", "scanner_score", "scanner_rank",
    "setup_type", "setup_state", "setup_quality", "trigger_price",
    "structural_stop", "risk_per_share",
)
DERIVED_FIELDS = (
    "distance_to_trigger_percent", "distance_to_stop_r", "time_of_day_bucket",
    "opportunity_age_seconds", "simultaneous_blocker_count",
    "blocker_transitions_before_decision", "opportunity_lifecycle_stage",
)
NEW_YORK = ZoneInfo("America/New_York")


def build_learning_examples(
    experiences: tuple[TradeOpportunityExperience, ...],
    outcomes: tuple[HorizonOutcome, ...],
    decisions: tuple[DecisionObservation, ...] = (),
    *,
    generation: ResearchGeneration | None = None,
) -> tuple[LearningExample, ...]:
    """Create one immutable example per meaningful decision point.

    Outcomes are attached only as labels.  Neither outcome values nor later
    decisions participate in feature extraction for an earlier cutoff.
    """

    by_outcome: dict[str, list[HorizonOutcome]] = defaultdict(list)
    for outcome in outcomes:
        by_outcome[outcome.experience_id].append(outcome)
    by_decision: dict[str, list[DecisionObservation]] = defaultdict(list)
    for decision in decisions:
        by_decision[decision.experience_id].append(decision)
    result: list[LearningExample] = []
    for experience in sorted(experiences, key=lambda item: (item.snapshot.decision_timestamp, item.experience_id)):
        points = sorted(by_decision[experience.experience_id], key=lambda item: (item.observed_at, item.decision_id))
        if not points:
            points = [_base_decision(experience)]
        first_at = min(experience.snapshot.decision_timestamp, *(item.observed_at for item in points))
        previous_blockers: tuple[str, ...] | None = None
        transitions = 0
        for point in points:
            if previous_blockers is not None and point.blockers != previous_blockers:
                transitions += 1
            previous_blockers = point.blockers
            partition = experience.partition if generation is None else generation.partition_for(experience.key.session_date)
            vector = extract_learning_features(experience, point, first_at=first_at, blocker_transitions=transitions)
            labels = labels_from_outcomes(tuple(by_outcome[experience.experience_id]))
            result.append(LearningExample(
                vector, labels, partition,
                point.atlas_decision in CHAMPION_SELECTIONS or point.actually_traded,
                tuple(sorted(point.blockers)), point.lifecycle_stage,
                point.technically_actionable, point.actually_traded,
            ))
    return tuple(result)


def extract_learning_features(
    experience: TradeOpportunityExperience,
    decision: DecisionObservation,
    *,
    first_at: datetime,
    blocker_transitions: int,
) -> LearningFeatureVector:
    snapshot = decision.snapshot
    cutoff = snapshot.decision_timestamp
    if decision.observed_at != cutoff or cutoff < experience.snapshot.decision_timestamp:
        raise ValueError("decision cutoff precedes immutable experience origin")
    values: dict[str, object] = {name: _scalar(getattr(snapshot, name)) for name in BASE_FIELDS}
    for name, value in snapshot.features:
        values[name] = _scalar(value)
    values.update({
        "distance_to_trigger_percent": _distance_percent(snapshot.last_price, snapshot.trigger_price),
        "distance_to_stop_r": _distance_r(snapshot.last_price, snapshot.structural_stop, snapshot.risk_per_share),
        "time_since_session_open_seconds": _time_since_open(cutoff, experience.key.session),
        "time_of_day_bucket": _time_bucket(cutoff),
        "opportunity_age_seconds": max(0, int((cutoff - first_at).total_seconds())),
        "simultaneous_blocker_count": len(decision.blockers),
        "blocker_transitions_before_decision": blocker_transitions,
        "opportunity_lifecycle_stage": decision.lifecycle_stage,
        "atlas_decision_state": decision.atlas_decision.value,
        "session": experience.key.session,
        "passed_rule_set": "|".join(sorted(snapshot.passed_rules)),
        "failed_rule_set": "|".join(sorted(snapshot.failed_rules)),
        "blocker_set": "|".join(sorted(decision.blockers)),
    })
    sources = dict(snapshot.feature_source_timestamps)
    for name in BASE_FIELDS:
        if values[name] is not None:
            timestamp = snapshot.source_timestamp or cutoff
            sources.setdefault(name, timestamp)
    for name in DERIVED_FIELDS:
        if name in values and values[name] is not None:
            sources.setdefault(name, cutoff)
    if any(timestamp > cutoff for timestamp in sources.values()):
        raise ValueError("anti-lookahead violation while extracting learning features")
    ordered = tuple(sorted(values.items()))
    missing = tuple(sorted(name for name, value in ordered if value is None))
    decision_id = decision.decision_id
    return LearningFeatureVector(
        experience.experience_id, decision_id, cutoff, experience.key.session_date,
        experience.key.symbol.upper(), experience.key.session.upper(), ordered,
        tuple(sorted(sources.items())), missing,
    )


def labels_from_outcomes(outcomes: tuple[HorizonOutcome, ...]) -> LearningLabels | None:
    eligible = [
        item for item in outcomes
        if item.status is OutcomeStatus.COMPLETE
        and item.reached_1r is not None
        and item.reached_2r is not None
        and item.reached_3r is not None
        and item.stop_reached is not None
    ]
    if not eligible:
        return None
    label = max(eligible, key=lambda item: item.horizon_minutes)
    stop_first = label.first_plan_event == "STOP" and not label.reached_1r
    expected_r = _deterministic_r(label)
    return LearningLabels(
        bool(label.reached_1r and not stop_first),
        bool(label.reached_2r and label.first_plan_event != "STOP"),
        bool(label.reached_3r and label.first_plan_event != "STOP"),
        stop_first,
        _float(label.mfe_r), _float(label.mae_r), expected_r,
        label.time_to_1r_seconds, label.time_to_2r_seconds, label.horizon_minutes,
    )


def assess_sufficiency(
    examples: tuple[LearningExample, ...], target: LearningTarget,
    policy: ResearchEvidencePolicy,
) -> SufficiencyAssessment:
    unique = _latest_by_experience(examples)
    complete = [item for item in unique if item.labels is not None]
    positives = sum(target_value(item.labels, target) for item in complete)
    negatives = len(complete) - positives
    partitions = {part: sum(item.partition is part and item.labels is not None for item in unique) for part in DatasetPartition}
    dates = {item.features.session_date for item in complete}
    symbols = {item.features.symbol for item in complete}
    sessions = {item.features.session for item in complete}
    checks = (
        (len(unique) >= policy.minimum_total, f"total<{policy.minimum_total}"),
        (partitions[DatasetPartition.TRAIN] >= policy.minimum_train, f"train<{policy.minimum_train}"),
        (partitions[DatasetPartition.VALIDATION] >= policy.minimum_validation, f"validation<{policy.minimum_validation}"),
        (partitions[DatasetPartition.HOLDOUT] >= policy.minimum_holdout, f"holdout<{policy.minimum_holdout}"),
        (positives >= policy.minimum_positive, f"positives<{policy.minimum_positive}"),
        (negatives >= policy.minimum_negative, f"negatives<{policy.minimum_negative}"),
        (len(dates) >= policy.minimum_unique_dates, f"dates<{policy.minimum_unique_dates}"),
        (len(symbols) >= policy.minimum_unique_symbols, f"symbols<{policy.minimum_unique_symbols}"),
        (len(sessions) >= policy.minimum_unique_sessions, f"sessions<{policy.minimum_unique_sessions}"),
    )
    reasons = tuple(message for passed, message in checks if not passed)
    return SufficiencyAssessment(
        EvidenceStatus.SUFFICIENT if not reasons else EvidenceStatus.INSUFFICIENT_EVIDENCE,
        reasons, len(unique), len(complete), positives, negatives,
        len(dates), len(symbols), len(sessions),
    )


def target_value(labels: LearningLabels, target: LearningTarget) -> bool:
    return {
        LearningTarget.ONE_R_BEFORE_STOP: labels.one_r_before_stop,
        LearningTarget.TWO_R_BEFORE_STOP: labels.two_r_before_stop,
        LearningTarget.THREE_R_BEFORE_STOP: labels.three_r_before_stop,
        LearningTarget.STOP_BEFORE_ONE_R: labels.stop_before_one_r,
    }[target]


def feature_digest(vector: LearningFeatureVector) -> str:
    return sha256(canonical_json({"version": vector.feature_version, "values": vector.values,
                                  "missing": vector.missing, "cutoff": vector.decision_timestamp}).encode()).hexdigest()


def latest_by_experience(examples: tuple[LearningExample, ...]) -> tuple[LearningExample, ...]:
    return _latest_by_experience(examples)


def _latest_by_experience(examples):
    result = {}
    for item in examples:
        identity = item.features.experience_id
        existing = result.get(identity)
        if existing is None or item.features.decision_timestamp > existing.features.decision_timestamp:
            result[identity] = item
    return tuple(result[key] for key in sorted(result))


def _base_decision(exp: TradeOpportunityExperience) -> DecisionObservation:
    return DecisionObservation(
        exp.experience_id, exp.snapshot.decision_timestamp, exp.source_event_identity,
        exp.atlas_decision, exp.snapshot, exp.blockers, exp.technically_actionable,
        exp.actually_traded, exp.key.symbol, "INITIAL",
    )


def _scalar(value):
    return None if value is None else float(value) if isinstance(value, Decimal) else value


def _float(value):
    return None if value is None else float(value)


def _distance_percent(price, anchor):
    if price is None or anchor in (None, 0):
        return None
    return float((price - anchor) / anchor * Decimal(100))


def _distance_r(price, anchor, risk):
    if price is None or anchor is None or risk in (None, 0):
        return None
    return float((price - anchor) / risk)


def _time_since_open(value: datetime, session: str) -> int | None:
    if session.upper() != "REGULAR":
        return None
    local = value.astimezone(NEW_YORK)
    opened = local.replace(hour=9, minute=30, second=0, microsecond=0)
    return max(0, int((local - opened).total_seconds()))


def _time_bucket(value: datetime) -> str:
    local = value.astimezone(NEW_YORK)
    minute = local.hour * 60 + local.minute
    if minute < 11 * 60:
        return "EARLY"
    if minute < 14 * 60:
        return "MID"
    return "LATE"


def _deterministic_r(outcome: HorizonOutcome) -> float | None:
    if outcome.first_plan_event == "STOP":
        return -1.0
    if outcome.reached_3r:
        return 3.0
    if outcome.reached_2r:
        return 2.0
    if outcome.reached_1r:
        return 1.0
    return None
