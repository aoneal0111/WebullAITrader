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

__all__ = [
    "ACTIVE_STRATEGIES", "EPISODE_SCHEMA_VERSION", "LABELING_VERSION", "DEDUPE_VERSION",
    "KnowledgeEpisode", "QuarantineRecord", "KnowledgeStore", "JsonlBarProvider", "build_corpus", "build_reentry_label",
    "expectancy", "maximum_drawdown", "profit_factor", "report", "validate_corpus",
    "AcquisitionConfig", "AlpacaHistoricalClient", "HistoricalBar", "PartitionManifest",
    "detect_intraday_gaps", "download_partition", "normalize_bar", "validate_source_bars",
    "CandidateDay", "discover_candidate_days", "ResearchOrchestrator", "RunPlan", "plan_summary",
    "status_for_root",
]
