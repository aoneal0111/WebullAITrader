"""Transparent research challengers with no production dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, log, sqrt
from typing import Protocol

import numpy as np

from .contracts import (
    ChallengerPrediction, EvidenceStatus, LearningExample, LearningTarget,
    ResearchEvidencePolicy, ResearchRecommendation, ResearchRiskTier,
)
from .dataset import assess_sufficiency, latest_by_experience, target_value

ANALOG_DIMENSIONS = (
    "setup_type", "setup_state", "session", "catalyst_status",
    "spread_percent", "relative_volume", "float_shares",
    "pullback_depth_percent", "higher_low",
    "pullback_volume_contraction_ratio",
    "recent_momentum_velocity_percent_per_minute", "distance_from_hod_percent",
)


class ResearchChallenger(Protocol):
    challenger_id: str
    target: LearningTarget

    def predict(self, features, *, as_of=None) -> ChallengerPrediction: ...


class HistoricalAnalogChallenger:
    challenger_id = "HISTORICAL_ANALOG_CHALLENGER_V1"

    def __init__(self, examples: tuple[LearningExample, ...], target: LearningTarget,
                 policy: ResearchEvidencePolicy = ResearchEvidencePolicy()) -> None:
        self.target = target
        self.policy = policy
        self._examples = tuple(sorted(examples, key=lambda item: (item.features.decision_timestamp, item.features.decision_id)))

    def predict(self, features, *, as_of=None) -> ChallengerPrediction:
        cutoff = features.decision_timestamp if as_of is None else as_of
        target_values = features.as_mapping()
        matches_by_experience = {}
        matched_dimensions = []
        for candidate in self._examples:
            if candidate.features.decision_timestamp >= cutoff or candidate.labels is None:
                continue
            candidate_values = candidate.features.as_mapping()
            shared = tuple(name for name in ANALOG_DIMENSIONS if _bucket(name, candidate_values.get(name)) == _bucket(name, target_values.get(name)))
            if len(shared) == len(ANALOG_DIMENSIONS):
                matches_by_experience[candidate.features.experience_id] = candidate
                matched_dimensions = list(shared)
        matches = tuple(matches_by_experience.values())
        positives = sum(target_value(item.labels, self.target) for item in matches)
        sufficient = len(matches) >= self.policy.minimum_analogs
        return _prediction(
            self.challenger_id, self.target, positives, len(matches), sufficient,
            tuple(f"matched:{name}={_bucket(name, target_values.get(name))}" for name in matched_dimensions),
        )


class EmpiricalCohortChallenger:
    challenger_id = "EMPIRICAL_COHORT_CHALLENGER_V1"
    DIMENSIONS = ("setup_type", "session", "catalyst_status", "spread_percent", "relative_volume")

    def __init__(self, train: tuple[LearningExample, ...], target: LearningTarget,
                 policy: ResearchEvidencePolicy = ResearchEvidencePolicy()) -> None:
        if any(item.partition.value != "TRAIN" for item in train):
            raise ValueError("empirical challenger may fit TRAIN only")
        self.target = target
        self.policy = policy
        self._cohorts: dict[tuple[str, ...], tuple[int, int]] = {}
        grouped: dict[tuple[str, ...], list[LearningExample]] = {}
        for item in latest_by_experience(train):
            if item.labels is None:
                continue
            key = self._key(item.features.as_mapping())
            grouped.setdefault(key, []).append(item)
        for key, rows in grouped.items():
            self._cohorts[key] = (sum(target_value(item.labels, target) for item in rows), len(rows))

    def predict(self, features, *, as_of=None) -> ChallengerPrediction:
        key = self._key(features.as_mapping())
        positives, total = self._cohorts.get(key, (0, 0))
        return _prediction(
            self.challenger_id, self.target, positives, total,
            total >= self.policy.minimum_cohort,
            tuple(f"cohort:{name}={value}" for name, value in zip(self.DIMENSIONS, key)),
        )

    def _key(self, values) -> tuple[str, ...]:
        return tuple(_bucket(name, values.get(name)) for name in self.DIMENSIONS)


@dataclass(frozen=True, slots=True)
class TransparentEncoder:
    numeric: tuple[str, ...]
    categories: tuple[tuple[str, tuple[str, ...]], ...]
    medians: tuple[tuple[str, float], ...]

    @classmethod
    def fit(cls, examples: tuple[LearningExample, ...], maximum_features: int) -> "TransparentEncoder":
        names = sorted({name for item in examples for name, _ in item.features.values})
        numeric = []
        categorical = []
        medians = []
        for name in names:
            observed = [item.features.as_mapping().get(name) for item in examples]
            present = [value for value in observed if value is not None]
            if present and all(isinstance(value, (int, float, bool)) for value in present):
                numeric.append(name)
                ordered = sorted(float(value) for value in present)
                medians.append((name, ordered[len(ordered) // 2]))
            elif present:
                categorical.append((name, tuple(sorted({str(value) for value in present}))))
        # Deterministic bound protects sample/feature ratio. Missing indicators
        # are paired with each numeric feature and count toward this budget.
        numeric = numeric[: max(0, maximum_features // 2)]
        remaining = max(0, maximum_features - 2 * len(numeric))
        bounded_categories = []
        for name, values in categorical:
            kept = values[:remaining]
            if kept:
                bounded_categories.append((name, kept))
                remaining -= len(kept)
            if remaining == 0:
                break
        return cls(tuple(numeric), tuple(bounded_categories), tuple((name, value) for name, value in medians if name in numeric))

    @property
    def feature_names(self) -> tuple[str, ...]:
        result = []
        for name in self.numeric:
            result.extend((name, f"{name}__MISSING"))
        for name, values in self.categories:
            result.extend(f"{name}=={value}" for value in values)
        return tuple(result)

    def transform(self, examples) -> np.ndarray:
        medians = dict(self.medians)
        rows = []
        for item in examples:
            vector = item.features if hasattr(item, "features") else item
            values = vector.as_mapping()
            row = []
            for name in self.numeric:
                value = values.get(name)
                row.extend((medians[name] if value is None else float(value), float(value is None)))
            for name, categories in self.categories:
                value = values.get(name)
                row.extend(float(str(value) == category) for category in categories)
            rows.append(row)
        return np.asarray(rows, dtype=float)


class SimpleLogisticChallenger:
    challenger_id = "SIMPLE_LOGISTIC_CHALLENGER_V1"

    def __init__(self, target: LearningTarget, policy: ResearchEvidencePolicy,
                 encoder: TransparentEncoder | None = None, weights: np.ndarray | None = None,
                 assessment=None, regularization: float = 1.0) -> None:
        self.target = target
        self.policy = policy
        self.encoder = encoder
        self.weights = weights
        self.assessment = assessment
        self.regularization = regularization

    @classmethod
    def fit(cls, all_examples: tuple[LearningExample, ...], target: LearningTarget,
            policy: ResearchEvidencePolicy = ResearchEvidencePolicy(), *,
            regularization: float = 1.0, iterations: int = 500, learning_rate: float = 0.05):
        assessment = assess_sufficiency(all_examples, target, policy)
        train = latest_by_experience(tuple(item for item in all_examples if item.partition.value == "TRAIN" and item.labels is not None))
        if assessment.status is EvidenceStatus.INSUFFICIENT_EVIDENCE:
            return cls(target, policy, assessment=assessment, regularization=regularization)
        maximum = max(1, len(train) // policy.examples_per_fitted_feature)
        encoder = TransparentEncoder.fit(train, maximum)
        x = encoder.transform(train)
        if x.shape[1] == 0:
            return cls(target, policy, assessment=assessment, regularization=regularization)
        x = np.column_stack((np.ones(len(x)), x))
        y = np.asarray([target_value(item.labels, target) for item in train], dtype=float)
        weights = np.zeros(x.shape[1], dtype=float)
        for _ in range(iterations):
            probabilities = 1.0 / (1.0 + np.exp(np.clip(-(x @ weights), -35, 35)))
            penalty = np.r_[0.0, weights[1:]] * regularization / len(x)
            weights -= learning_rate * ((x.T @ (probabilities - y)) / len(x) + penalty)
        return cls(target, policy, encoder, weights, assessment, regularization)

    def predict(self, features, *, as_of=None) -> ChallengerPrediction:
        if self.encoder is None or self.weights is None:
            reasons = () if self.assessment is None else self.assessment.reasons
            return _insufficient(self.challenger_id, self.target, reasons)
        x = self.encoder.transform((features,))[0]
        probability = _sigmoid(float(np.r_[1.0, x] @ self.weights))
        explanation = tuple(
            f"{name}:{weight:+.6f}" for name, weight in sorted(
                zip(("INTERCEPT",) + self.encoder.feature_names, self.weights),
                key=lambda item: abs(item[1]), reverse=True,
            )[:10]
        )
        return _direct_prediction(self.challenger_id, self.target, probability, explanation)


class CalibratedScoreChallenger:
    """Platt calibration fitted on VALIDATION only; HOLDOUT is never accepted."""

    challenger_id = "CALIBRATED_SCORE_CHALLENGER_V1"

    def __init__(self, base: SimpleLogisticChallenger, slope: float | None, intercept: float | None) -> None:
        self.base = base
        self.target = base.target
        self.slope = slope
        self.intercept = intercept

    @classmethod
    def fit(cls, base: SimpleLogisticChallenger, validation: tuple[LearningExample, ...]):
        if any(item.partition.value != "VALIDATION" for item in validation):
            raise ValueError("calibration may use VALIDATION only; HOLDOUT tuning is forbidden")
        usable = [item for item in latest_by_experience(validation) if item.labels is not None]
        if base.weights is None or len(usable) < base.policy.minimum_validation:
            return cls(base, None, None)
        scores = np.asarray([base.predict(item.features).probability for item in usable], dtype=float)
        logits = np.log(np.clip(scores, 1e-9, 1 - 1e-9) / np.clip(1 - scores, 1e-9, 1))
        y = np.asarray([target_value(item.labels, base.target) for item in usable], dtype=float)
        slope = intercept = 0.0
        for _ in range(300):
            p = 1 / (1 + np.exp(np.clip(-(slope * logits + intercept), -35, 35)))
            slope -= 0.03 * (float(np.mean((p - y) * logits)) + 0.01 * slope)
            intercept -= 0.03 * float(np.mean(p - y))
        return cls(base, slope, intercept)

    def predict(self, features, *, as_of=None) -> ChallengerPrediction:
        raw = self.base.predict(features)
        if raw.probability is None or self.slope is None or self.intercept is None:
            return _insufficient(self.challenger_id, self.target, ("calibration evidence insufficient",))
        logit = log(max(1e-9, raw.probability) / max(1e-9, 1 - raw.probability))
        probability = _sigmoid(self.slope * logit + self.intercept)
        return _direct_prediction(self.challenger_id, self.target, probability, (
            f"base_probability={raw.probability:.6f}", f"platt_slope={self.slope:.6f}",
            f"platt_intercept={self.intercept:.6f}",
        ))


def _prediction(challenger, target, positives, total, sufficient, explanation):
    if not sufficient:
        return _insufficient(challenger, target, (f"sample<{total if total else 1}",), total)
    probability = positives / total
    low, high = _wilson(positives, total)
    direct = _direct_prediction(challenger, target, probability, explanation, total)
    return ChallengerPrediction(direct.challenger_id, direct.target, direct.opportunity_score,
                                probability, low, high, direct.evidence_status,
                                direct.recommendation, direct.risk_tier, total, float(total), direct.explanation)


def _direct_prediction(challenger, target, probability, explanation, total=0):
    if probability >= 0.75:
        recommendation, tier = ResearchRecommendation.RESEARCH_HIGH_CONFIDENCE, ResearchRiskTier.HIGH_CONFIDENCE
    elif probability >= 0.6:
        recommendation, tier = ResearchRecommendation.RESEARCH_FAVORABLE, ResearchRiskTier.MODERATE_CONFIDENCE
    elif probability >= 0.4:
        recommendation, tier = ResearchRecommendation.RESEARCH_WATCH, ResearchRiskTier.LOW_CONFIDENCE
    else:
        recommendation, tier = ResearchRecommendation.RESEARCH_SKIP, ResearchRiskTier.NO_RISK_RECOMMENDATION
    return ChallengerPrediction(challenger, target, probability, probability, None, None,
                                EvidenceStatus.SUFFICIENT, recommendation, tier, total, float(total), tuple(explanation))


def _insufficient(challenger, target, reasons, total=0):
    return ChallengerPrediction(challenger, target, None, None, None, None,
                                EvidenceStatus.INSUFFICIENT_EVIDENCE,
                                ResearchRecommendation.RESEARCH_WATCH,
                                ResearchRiskTier.NO_RISK_RECOMMENDATION,
                                total, float(total), tuple(reasons))


def _bucket(name, value):
    if value is None:
        return "UNAVAILABLE"
    bounds = {"spread_percent": (0.2, .5, 1, 2), "relative_volume": (1, 2, 5, 10),
              "float_shares": (1e6, 5e6, 20e6, 100e6), "pullback_depth_percent": (1, 2, 4, 8),
              "pullback_volume_contraction_ratio": (.5, .8, 1, 1.5),
              "recent_momentum_velocity_percent_per_minute": (-1, 0, 1, 3),
              "distance_from_hod_percent": (-8, -4, -2, -1, 0)}.get(name)
    if bounds is None or isinstance(value, (str, bool)):
        return str(value)
    for bound in bounds:
        if float(value) <= bound:
            return f"LE_{bound}"
    return f"GT_{bounds[-1]}"


def _sigmoid(value):
    return 1 / (1 + exp(-max(-35, min(35, value))))


def _wilson(successes, total):
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)
