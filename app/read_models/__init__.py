"""Immutable consumer-facing projections of authoritative application state.

Read models are deterministic, side-effect-free transformations. They do not
place orders, call brokers, persist data, or depend on GUI frameworks.
"""

from app.read_models.decisions import (
    DecisionProjector,
    DecisionReadModel,
    DecisionsReadModelSnapshot,
)
from app.read_models.operator_workspace import (
    OperatorWorkspaceProjector,
    OperatorWorkspaceSnapshot,
    WorkspaceSelectionSource,
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
from app.read_models.trade_lifecycle import (
    TradeLifecycle,
    TradeLifecycleEntry,
    TradeLifecyclePhase,
    TradeLifecycleProjector,
    TradeLifecycleSnapshot,
    TradeLifecycleStatus,
)

__all__ = [
    "DecisionProjector",
    "DecisionReadModel",
    "DecisionsReadModelSnapshot",
    "HealthMetric",
    "OverallHealth",
    "OperatorWorkspaceProjector",
    "OperatorWorkspaceSnapshot",
    "RuntimeHealthProjector",
    "RuntimeHealthSnapshot",
    "SubsystemHealth",
    "TimelineCategory",
    "TimelineProjector",
    "TimelineReadModelSnapshot",
    "TimelineSeverity",
    "TradeLifecycle",
    "TradeLifecycleEntry",
    "TradeLifecyclePhase",
    "TradeLifecycleProjector",
    "TradeLifecycleSnapshot",
    "TradeLifecycleStatus",
    "WorkspaceSelectionSource",
]
