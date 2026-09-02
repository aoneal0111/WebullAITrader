"""Immutable research-only contracts for the Atlas Opportunity Learning Engine.

Nothing in this package is an execution instruction.  Recommendation names are
deliberately distinct from production strategy states.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Mapping

from app.trade_intelligence.models import DatasetPartition, FEATURE_VERSION, ResearchGeneration

LEARNING_FEATURE_VERSION = "ATLAS_LEARNING_FEATURES_V1"
LABEL_VERSION = "ATLAS_PLAN_PATH_LABELS_V1"
RESEARCH_POLICY_VERSION = "ATLAS_RESEARCH_GATES_V1"
MODEL_ARTIFACT_VERSION = "ATLAS_RESEARCH_MODEL_V1"


class EvidenceStatus(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ResearchRecommendation(StrEnum):
    RESEARCH_SKIP = "RESEARCH_SKIP"
    RESEARCH_WATCH = "RESEARCH_WATCH"
    RESEARCH_FAVORABLE = "RESEARCH_FAVORABLE"
    RESEARCH_HIGH_CONFIDENCE = "RESEARCH_HIGH_CONFIDENCE"


class ResearchRiskTier(StrEnum):
    NO_RISK_RECOMMENDATION = "NO_RISK_RECOMMENDATION"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    MODERATE_CONFIDENCE = "MODERATE_CONFIDENCE"
    HIGH_CONFIDENCE = "HIGH_CONFIDENCE"


class LearningTarget(StrEnum):
    ONE_R_BEFORE_STOP = "1R_BEFORE_STOP"
    TWO_R_BEFORE_STOP = "2R_BEFORE_STOP"
    THREE_R_BEFORE_STOP = "3R_BEFORE_STOP"
    STOP_BEFORE_ONE_R = "STOP_BEFORE_1R"


@dataclass(frozen=True, slots=True)
class ResearchEvidencePolicy:
    """Versioned, deliberately conservative small-sample gates."""

    version: str = RESEARCH_POLICY_VERSION
    minimum_total: int = 200
    minimum_train: int = 120
    minimum_validation: int = 40
    minimum_holdout: int = 40
    minimum_positive: int = 30
    minimum_negative: int = 30
    minimum_unique_dates: int = 10
    minimum_unique_symbols: int = 20
    minimum_unique_sessions: int = 3
    minimum_cohort: int = 20
    minimum_analogs: int = 20
    examples_per_fitted_feature: int = 10

    def __post_init__(self) -> None:
        numeric = tuple(value for name, value in vars_for_slots(self).items() if name != "version")
        if not self.version or any(not isinstance(value, int) or value <= 0 for value in numeric):
            raise ValueError("research evidence policy requires a version and positive gates")


@dataclass(frozen=True, slots=True)
class LearningGeneration:
    """Frozen Phase 1 generation plus immutable selection/evaluation protocol."""

    base: ResearchGeneration
    label_version: str
    model_family_version: str
    hyperparameters: tuple[tuple[str, str], ...]
    selection_criteria: tuple[str, ...]
    evaluation_criteria: tuple[str, ...]
    research_policy: ResearchEvidencePolicy = field(default_factory=ResearchEvidencePolicy)

    def __post_init__(self) -> None:
        if self.base.feature_version != FEATURE_VERSION:
            raise ValueError("learning generation must reference the captured feature version")
        if self.label_version != LABEL_VERSION or not self.model_family_version:
            raise ValueError("learning generation versions are required")
        if len(dict(self.hyperparameters)) != len(self.hyperparameters):
            raise ValueError("hyperparameter names must be unique")
        if not self.selection_criteria or not self.evaluation_criteria:
            raise ValueError("selection and evaluation criteria must be frozen")

    @property
    def generation_id(self) -> str:
        return self.base.generation_id


FeatureScalar = float | int | bool | str | None


@dataclass(frozen=True, slots=True)
class LearningFeatureVector:
    experience_id: str
    decision_id: str
    decision_timestamp: datetime
    session_date: date
    symbol: str
    session: str
    values: tuple[tuple[str, FeatureScalar], ...]
    source_timestamps: tuple[tuple[str, datetime], ...]
    missing: tuple[str, ...]
    feature_version: str = LEARNING_FEATURE_VERSION

    def __post_init__(self) -> None:
        if not self.experience_id or not self.decision_id or not self.symbol:
            raise ValueError("feature identity is required")
        if self.feature_version != LEARNING_FEATURE_VERSION:
            raise ValueError("unsupported learning feature version")
        if self.decision_timestamp.tzinfo is None:
            raise ValueError("decision timestamp must be timezone-aware")
        names = [name for name, _ in self.values]
        if len(names) != len(set(names)):
            raise ValueError("feature names must be unique")
        if tuple(sorted(self.missing)) != self.missing:
            raise ValueError("missing feature names must be sorted")
        if any(timestamp.tzinfo is None or timestamp > self.decision_timestamp for _, timestamp in self.source_timestamps):
            raise ValueError("anti-lookahead violation in learning feature sources")

    def as_mapping(self) -> dict[str, FeatureScalar]:
        return dict(self.values)


@dataclass(frozen=True, slots=True)
class LearningLabels:
    one_r_before_stop: bool
    two_r_before_stop: bool
    three_r_before_stop: bool
    stop_before_one_r: bool
    mfe_r: float | None
    mae_r: float | None
    expected_return_r: float | None
    time_to_1r_seconds: int | None
    time_to_2r_seconds: int | None
    complete_horizon_minutes: int
    label_version: str = LABEL_VERSION


@dataclass(frozen=True, slots=True)
class LearningExample:
    features: LearningFeatureVector
    labels: LearningLabels | None
    partition: DatasetPartition
    champion_selected: bool
    blockers: tuple[str, ...]
    lifecycle_stage: str
    technically_actionable: bool
    actually_traded: bool


@dataclass(frozen=True, slots=True)
class SufficiencyAssessment:
    status: EvidenceStatus
    reasons: tuple[str, ...]
    total: int
    complete: int
    positives: int
    negatives: int
    unique_dates: int
    unique_symbols: int
    unique_sessions: int


@dataclass(frozen=True, slots=True)
class ChallengerPrediction:
    challenger_id: str
    target: LearningTarget
    opportunity_score: float | None
    probability: float | None
    confidence_low: float | None
    confidence_high: float | None
    evidence_status: EvidenceStatus
    recommendation: ResearchRecommendation
    risk_tier: ResearchRiskTier
    sample_size: int
    effective_sample_size: float
    explanation: tuple[str, ...]

    def __post_init__(self) -> None:
        for value in (self.opportunity_score, self.probability, self.confidence_low, self.confidence_high):
            if value is not None and not 0 <= value <= 1:
                raise ValueError("probability-like challenger output must be in [0, 1]")


def vars_for_slots(value: object) -> Mapping[str, object]:
    return {name: getattr(value, name) for name in value.__dataclass_fields__}
