"""Atlas multi-strategy discovery: bounded, pure, and research-only."""

from .context import build_impulse, build_pullback, build_reference_levels, structural_anchor
from .capabilities import feature_capability_report
from .benchmark import run_cardinality_benchmark
from .contracts import *
from .detectors import DetectorRegistry, ResearchDetector, default_registry
from .engine import DiscoveryMetrics, MultiStrategyDiscoveryEngine, normalize_detections
from .execution_adapter import (
    AdapterRejection, AdapterResult, ExecutionCandidate,
    MultiStrategyExecutionAdapter, PHASE1_EXECUTION_ALLOWLIST,
    PHASE1_INVALIDATION_CAPABILITIES, PHASE2_EXECUTION_ALLOWLIST,
    PHASE2_INVALIDATION_CAPABILITIES, PULLBACK_CONTINUATION_FAMILY,
    PULLBACK_CONTINUATION_ORDER, ACTIVE_STRATEGY_ORDER,
    FULL_EXECUTION_ALLOWLIST, FULL_INVALIDATION_CAPABILITIES,
)
from .integration import NormalizedOpportunityObserved, learning_membership_features, recommended_persistence_design
from .position_continuity import *
from .reporting import strategy_discovery_report
from .taxonomy import STRATEGY_TAXONOMY, taxonomy_by_id

__all__ = [name for name in globals() if not name.startswith("_")]
