"""Append-only integration contract for a future Trade Intelligence observer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .contracts import NormalizedOpportunity, TAXONOMY_VERSION

STRATEGY_MEMBERSHIP_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class NormalizedOpportunityObserved:
    """Future observation message; deliberately not wired into runtime in 2A.1."""

    opportunity: NormalizedOpportunity
    observed_at: datetime
    taxonomy_version: str = TAXONOMY_VERSION
    membership_schema_version: int = STRATEGY_MEMBERSHIP_SCHEMA_VERSION
    research_only: bool = True

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at != self.opportunity.decision_cutoff:
            raise ValueError("observation cutoff must equal normalized opportunity cutoff")
        if not self.research_only:
            raise ValueError("normalized opportunity observation is research-only")


def recommended_persistence_design() -> dict[str, object]:
    return {
        "choice": "SEPARATE_APPEND_ONLY_STRATEGY_MEMBERSHIP_RECORDS",
        "reason": "preserves Phase 1 experience payload identity and old dataset compatibility",
        "identity": "(normalized_opportunity_id,strategy_id,strategy_version,decision_cutoff)",
        "mutates_existing_experiences": False,
        "future_message": "NormalizedOpportunityObserved",
    }


def learning_membership_features(opportunity: NormalizedOpportunity):
    """Point-in-time categorical features for individual/combination studies."""

    identities = tuple(sorted(item.strategy_id for item in opportunity.memberships))
    return (
        ("discovery_primary_strategy", opportunity.primary_strategy_id),
        ("discovery_strategy_combination", "+".join(identities)),
        ("discovery_strategy_count", len(identities)),
        *((f"discovery_member_{identity}", True) for identity in identities),
    )
