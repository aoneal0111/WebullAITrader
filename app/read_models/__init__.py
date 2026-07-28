"""Immutable consumer-facing projections of authoritative application state.

Read models are deterministic, side-effect-free transformations. They do not
place orders, call brokers, persist data, or depend on GUI frameworks.
"""

from app.read_models.decisions import (
    DecisionProjector,
    DecisionReadModel,
    DecisionsReadModelSnapshot,
)
from app.read_models.runtime_health import (
    HealthMetric,
    OverallHealth,
    RuntimeHealthProjector,
    RuntimeHealthSnapshot,
    SubsystemHealth,
)
from app.read_models.timeline import (
    TimelineCategory,
    TimelineProjector,
    TimelineReadModelSnapshot,
    TimelineSeverity,
)

__all__ = [
    "DecisionProjector",
    "DecisionReadModel",
    "DecisionsReadModelSnapshot",
    "HealthMetric",
    "OverallHealth",
    "RuntimeHealthProjector",
    "RuntimeHealthSnapshot",
    "SubsystemHealth",
    "TimelineCategory",
    "TimelineProjector",
    "TimelineReadModelSnapshot",
    "TimelineSeverity",
]
