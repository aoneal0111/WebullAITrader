"""Research-only broad-market Dynamic Momentum Discovery."""

from .evaluator import (
    DynamicDiscoveryPolicy,
    evaluate_dynamic_momentum,
    production_comparison,
    semantic_signature,
    snapshot_from_rows,
)
from .comparison import (
    ProductionUniverseComparisonTracker,
    UniverseAdmissionObserverFanout,
)
from .experiments import summarize_breadths, summarize_selectivity
from .models import (
    BroadMarketSnapshot,
    DiscoverySource,
    DynamicMomentumObservation,
    MomentumEvent,
    ProductionUniverseComparison,
    SourceMembership,
)
from .outcomes import (
    DynamicMomentumOutcome,
    ForwardMarketPoint,
    label_dynamic_momentum_outcome,
)
from .provider import WebullBroadDiscoveryProvider
from .runtime import (
    DynamicMomentumDiscoveryRunner,
    DynamicMomentumDiscoveryRuntime,
    DynamicMomentumRuntimeMetrics,
)
from .service import DynamicMomentumDiscoveryService
from .store import JsonLinesDiscoveryStore

__all__ = [
    "BroadMarketSnapshot", "DiscoverySource", "DynamicDiscoveryPolicy",
    "DynamicMomentumDiscoveryRunner", "DynamicMomentumDiscoveryService",
    "DynamicMomentumDiscoveryRuntime", "DynamicMomentumRuntimeMetrics",
    "DynamicMomentumObservation", "DynamicMomentumOutcome", "ForwardMarketPoint",
    "JsonLinesDiscoveryStore", "MomentumEvent", "ProductionUniverseComparison",
    "SourceMembership", "WebullBroadDiscoveryProvider",
    "ProductionUniverseComparisonTracker", "UniverseAdmissionObserverFanout",
    "evaluate_dynamic_momentum", "label_dynamic_momentum_outcome",
    "production_comparison", "semantic_signature", "snapshot_from_rows",
    "summarize_breadths", "summarize_selectivity",
]
