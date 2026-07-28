"""Immutable consumer-facing projections of authoritative application state.

Read models are deterministic, side-effect-free transformations. They do not
place orders, call brokers, persist data, or depend on GUI frameworks.
"""

from app.read_models.decisions import (
    DecisionProjector,
    DecisionReadModel,
    DecisionsReadModelSnapshot,
)

__all__ = [
    "DecisionProjector",
    "DecisionReadModel",
    "DecisionsReadModelSnapshot",
]
