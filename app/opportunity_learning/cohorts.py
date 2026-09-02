"""Explainable setup, pullback, momentum, catalyst, and blocker cohorts."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import sqrt
from statistics import median

from .contracts import EvidenceStatus, LearningExample, ResearchEvidencePolicy


@dataclass(frozen=True, slots=True)
class CohortStatistics:
    cohort: str
    sample_size: int
    effective_sample_size: float
    evidence_status: EvidenceStatus
    one_r_rate: float | None
    two_r_rate: float | None
    three_r_rate: float | None
    stop_first_rate: float | None
    expected_mfe_r: float | None
    expected_mae_r: float | None
    median_mfe_r: float | None
    median_mae_r: float | None
    expected_return_r: float | None
    one_r_confidence_interval: tuple[float, float] | None
    symbols: tuple[tuple[str, int], ...]
    dates: tuple[tuple[str, int], ...]
    sessions: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class BlockerCohort:
    blocker: str
    context: str
    tied_blockers: tuple[str, ...]
    statistics: CohortStatistics
    causal_claim_allowed: bool


def analyze_feature_cohorts(
    examples: tuple[LearningExample, ...], dimensions: tuple[str, ...],
    policy: ResearchEvidencePolicy,
) -> tuple[CohortStatistics, ...]:
    grouped: dict[str, list[LearningExample]] = defaultdict(list)
    for example in examples:
        values = example.features.as_mapping()
        key = " + ".join(f"{name}={_bucket(name, values.get(name))}" for name in dimensions)
        grouped[key].append(example)
    return tuple(_statistics(key, tuple(rows), policy) for key, rows in sorted(grouped.items()))


def analyze_blockers(
    examples: tuple[LearningExample, ...], policy: ResearchEvidencePolicy,
) -> tuple[BlockerCohort, ...]:
    by_exp: dict[str, list[LearningExample]] = defaultdict(list)
    for item in examples:
        by_exp[item.features.experience_id].append(item)
    grouped: dict[tuple[str, str], list[LearningExample]] = defaultdict(list)
    ties: dict[tuple[str, str], set[str]] = defaultdict(set)
    for rows in by_exp.values():
        ordered = sorted(rows, key=lambda item: item.features.decision_timestamp)
        blocker_names = sorted({blocker for row in ordered for blocker in row.blockers})
        for blocker in blocker_names:
            presence = [blocker in row.blockers for row in ordered]
            tied = {other for row in ordered if blocker in row.blockers for other in row.blockers if other != blocker}
            contexts = {
                "INITIAL" if presence[0] else None,
                "EVER_APPEARED",
                "CLEARED" if any(presence) and not presence[-1] else "PERSISTENT",
                "SOLE" if any(row.blockers == (blocker,) for row in ordered) else "MULTIPLE",
            }
            representative = ordered[-1]
            for context in sorted(item for item in contexts if item is not None):
                grouped[(blocker, context)].append(representative)
                ties[(blocker, context)].update(tied)
    return tuple(
        BlockerCohort(blocker, context, tuple(sorted(ties[(blocker, context)])),
                      _statistics(f"{blocker}:{context}", tuple(rows), policy),
                      context == "SOLE")
        for (blocker, context), rows in sorted(grouped.items())
    )


def _statistics(name: str, examples: tuple[LearningExample, ...], policy: ResearchEvidencePolicy) -> CohortStatistics:
    # Decisions within one episode are correlated.  Count an experience at most
    # once in a cohort, using its latest matching decision point.
    by_experience = {}
    for item in examples:
        if item.labels is None:
            continue
        identity = item.features.experience_id
        existing = by_experience.get(identity)
        if existing is None or item.features.decision_timestamp > existing.features.decision_timestamp:
            by_experience[identity] = item
    complete = list(by_experience.values())
    n = len(complete)
    sufficient = n >= policy.minimum_cohort
    labels = [item.labels for item in complete]
    def rate(field):
        return None if not labels else sum(bool(getattr(item, field)) for item in labels) / len(labels)
    one = rate("one_r_before_stop")
    mfe = [item.mfe_r for item in labels if item.mfe_r is not None]
    mae = [item.mae_r for item in labels if item.mae_r is not None]
    returns = [item.expected_return_r for item in labels if item.expected_return_r is not None]
    return CohortStatistics(
        name, n, float(n), EvidenceStatus.SUFFICIENT if sufficient else EvidenceStatus.INSUFFICIENT_EVIDENCE,
        one, rate("two_r_before_stop"), rate("three_r_before_stop"), rate("stop_before_one_r"),
        _mean(mfe), _mean(mae), None if not mfe else median(mfe), None if not mae else median(mae),
        _mean(returns), None if one is None else _wilson(sum(item.one_r_before_stop for item in labels), n),
        _counts(item.features.symbol for item in complete),
        _counts(item.features.session_date.isoformat() for item in complete),
        _counts(item.features.session for item in complete),
    )


def _bucket(name: str, value) -> str:
    if value is None:
        return "UNAVAILABLE"
    bounds = {
        "pullback_depth_percent": (1, 2, 4, 8),
        "pullback_volume_contraction_ratio": (0.5, 0.8, 1.0, 1.5),
        "recent_momentum_velocity_percent_per_minute": (-1, 0, 1, 3),
        "distance_from_hod_percent": (-8, -4, -2, -1, 0),
        "spread_percent": (0.2, 0.5, 1, 2),
        "relative_volume": (1, 2, 5, 10),
        "float_shares": (1_000_000, 5_000_000, 20_000_000, 100_000_000),
    }.get(name)
    if bounds is None or isinstance(value, (str, bool)):
        return str(value)
    numeric = float(value)
    for bound in bounds:
        if numeric <= bound:
            return f"LE_{bound}"
    return f"GT_{bounds[-1]}"


def _wilson(successes: int, total: int) -> tuple[float, float]:
    if total == 0:
        return (0.0, 1.0)
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))


def _mean(values):
    return None if not values else sum(values) / len(values)


def _counts(values) -> tuple[tuple[str, int], ...]:
    result: dict[str, int] = defaultdict(int)
    for value in values:
        result[str(value)] += 1
    return tuple(sorted(result.items(), key=lambda item: (-item[1], item[0])))
