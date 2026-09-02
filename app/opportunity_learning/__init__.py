"""Atlas Opportunity Learning Engine: offline, explainable, research-only."""

from .artifacts import ResearchModelArtifact, publish_artifact, verify_artifact
from .challengers import (
    CalibratedScoreChallenger, EmpiricalCohortChallenger,
    HistoricalAnalogChallenger, ResearchChallenger, SimpleLogisticChallenger,
)
from .cohorts import BlockerCohort, CohortStatistics, analyze_blockers, analyze_feature_cohorts
from .contracts import (
    ChallengerPrediction, EvidenceStatus, LearningExample, LearningFeatureVector,
    LearningGeneration, LearningLabels, LearningTarget, ResearchEvidencePolicy,
    ResearchRecommendation, ResearchRiskTier,
)
from .dataset import assess_sufficiency, build_learning_examples, extract_learning_features, labels_from_outcomes
from .evaluation import ChampionComparison, EvaluationMetrics, compare_champion, evaluate_challenger, select_on_validation
from .pipeline import OfflineLearningPipeline, OfflineLearningResult
from .reporting import build_research_report
from .snapshot import ExternalSnapshot, ImmutableSnapshotReader, create_external_snapshot, file_identity, merge_snapshot_readers

__all__ = [name for name in globals() if not name.startswith("_")]
