"""Asset-neutral, research-only intelligence primitives.

Dependency direction is intentionally one way: asset-specific evidence layers may
import this package, while this package imports no scanner, broker, execution,
provider, persistence, runtime, or GUI code.
"""

from .contracts import (
    AssetResearchIdentity,
    EvidenceAvailability,
    EvidenceProvenance,
    OpportunityLifecycle,
    OpportunityLifecycleState,
    PerUnitRiskGeometry,
    ResearchDetection,
    ResearchDirection,
    ResearchOpportunity,
    ResearchOutcome,
    ScoreComponent,
)
from .detectors import Detector, DetectorDefinition, DetectorRegistry
from .lifecycle import remember_bounded, semantic_digest

__all__ = [
    "AssetResearchIdentity",
    "Detector",
    "DetectorDefinition",
    "DetectorRegistry",
    "EvidenceAvailability",
    "EvidenceProvenance",
    "OpportunityLifecycle",
    "OpportunityLifecycleState",
    "PerUnitRiskGeometry",
    "ResearchDetection",
    "ResearchDirection",
    "ResearchOpportunity",
    "ResearchOutcome",
    "ScoreComponent",
    "remember_bounded",
    "semantic_digest",
]
