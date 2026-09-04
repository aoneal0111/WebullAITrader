"""Research-only broad-market Dynamic Momentum Discovery."""

from .evaluator import (
    DynamicDiscoveryPolicy,
    evaluate_dynamic_momentum,
    production_comparison,
    semantic_signature,
    snapshot_from_rows,
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
from .runtime import DynamicMomentumDiscoveryRunner
from .service import DynamicMomentumDiscoveryService
from .store import JsonLinesDiscoveryStore

__all__ = [
    "BroadMarketSnapshot", "DiscoverySource", "DynamicDiscoveryPolicy",
    "DynamicMomentumDiscoveryRunner", "DynamicMomentumDiscoveryService",
    "DynamicMomentumObservation", "DynamicMomentumOutcome", "ForwardMarketPoint",
    "JsonLinesDiscoveryStore", "MomentumEvent", "ProductionUniverseComparison",
    "SourceMembership", "WebullBroadDiscoveryProvider",
    "evaluate_dynamic_momentum", "label_dynamic_momentum_outcome",
    "production_comparison", "semantic_signature", "snapshot_from_rows",
    "summarize_breadths", "summarize_selectivity",
]
