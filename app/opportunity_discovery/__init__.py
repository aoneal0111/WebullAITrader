"""Atlas multi-strategy discovery: bounded, pure, and research-only."""

from .context import build_impulse, build_pullback, build_reference_levels, structural_anchor
from .capabilities import feature_capability_report
from .benchmark import run_cardinality_benchmark
from .contracts import *
from .detectors import DetectorRegistry, ResearchDetector, default_registry
from .engine import DiscoveryMetrics, MultiStrategyDiscoveryEngine, normalize_detections
from .integration import NormalizedOpportunityObserved, learning_membership_features, recommended_persistence_design
from .position_continuity import *
from .reporting import strategy_discovery_report
from .taxonomy import STRATEGY_TAXONOMY, taxonomy_by_id

__all__ = [name for name in globals() if not name.startswith("_")]
