"""Offline, research-only Trading Knowledge Pack V1."""

from .models import (
    ACTIVE_STRATEGIES, EPISODE_SCHEMA_VERSION, LABELING_VERSION, DEDUPE_VERSION,
    KnowledgeEpisode, QuarantineRecord,
)
from .storage import KnowledgeStore
from .mining import JsonlBarProvider, build_corpus, build_reentry_label
from .reporting import expectancy, maximum_drawdown, profit_factor, report, validate_corpus
from .acquisition import (AcquisitionConfig, AlpacaHistoricalClient, HistoricalBar, PartitionManifest,
                          detect_intraday_gaps, download_partition, normalize_bar, validate_source_bars)
from .candidate_days import CandidateDay, discover_candidate_days
from .orchestration import ResearchOrchestrator, RunPlan, plan_summary
from .accounting import status_for_root
from .features import (FEATURE_DERIVATION_VERSION, FEATURE_SCHEMA_VERSION, derive_point_in_time_features,
                       feature_snapshot, prefix_invariant_features)
from .analysis import (GROUP_DIMENSIONS, cohort_report, chronological_splits, entry_delay_research,
                       first_tranche_report, profit_management_research, walk_forward_folds,
                       PROFIT_RESEARCH_VERSION, REENTRY_TRANSITION_VERSION, simulate_profit_policy,
                       runner_path_analysis, capital_scenarios, constant_risk_scenarios,
                       transition_record, transition_matrix, hold_vs_reentry)

__all__ = [
    "ACTIVE_STRATEGIES", "EPISODE_SCHEMA_VERSION", "LABELING_VERSION", "DEDUPE_VERSION",
    "KnowledgeEpisode", "QuarantineRecord", "KnowledgeStore", "JsonlBarProvider", "build_corpus", "build_reentry_label",
    "expectancy", "maximum_drawdown", "profit_factor", "report", "validate_corpus",
    "AcquisitionConfig", "AlpacaHistoricalClient", "HistoricalBar", "PartitionManifest",
    "detect_intraday_gaps", "download_partition", "normalize_bar", "validate_source_bars",
    "CandidateDay", "discover_candidate_days", "ResearchOrchestrator", "RunPlan", "plan_summary",
    "status_for_root",
    "FEATURE_SCHEMA_VERSION", "FEATURE_DERIVATION_VERSION", "derive_point_in_time_features", "feature_snapshot", "prefix_invariant_features",
    "GROUP_DIMENSIONS", "cohort_report", "chronological_splits", "walk_forward_folds", "entry_delay_research", "profit_management_research", "first_tranche_report",
    "PROFIT_RESEARCH_VERSION", "REENTRY_TRANSITION_VERSION", "simulate_profit_policy", "runner_path_analysis",
    "capital_scenarios", "constant_risk_scenarios", "transition_record", "transition_matrix", "hold_vs_reentry",
]
