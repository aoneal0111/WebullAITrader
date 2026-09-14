"""Typed constants and validation models for the DI-1 offline artifact."""

from dataclasses import dataclass

ARTIFACT_SCHEMA_VERSION = "ATLAS_HISTORICAL_DECISION_INTELLIGENCE_V1"
COMPONENT_VERSIONS = (
    "ATLAS_PROFIT_RESEARCH_V1",
    "ATLAS_REENTRY_TRANSITIONS_V1",
    "ATLAS_PIT_PROFITABILITY_CONTEXT_V1",
    "ATLAS_BENCHMARK_REGIME_V1",
    "ATLAS_BAR_EXECUTION_RESEARCH_V1",
)
EXPECTED_EPISODES = 719_957
EXPECTED_MEMBERSHIPS = 1_582_847
EXPECTED_STRATEGIES = 23
REQUIRED_SECTIONS = (
    "strategy_scorecards", "temporal", "walk_forward", "profit_research",
    "reentry", "failure_analysis", "context_analysis", "execution_research",
)
CONFIDENCE_STATES = {
    "STRONG_EVIDENCE", "MODERATE_EVIDENCE", "WEAK_EVIDENCE",
    "INSUFFICIENT_SAMPLE", "MISSING_CONTEXT", "UNAVAILABLE_SOURCE",
    "LARGE_SAMPLE", "MODERATE", "SMALL_SAMPLE", "LOW_SAMPLE",
}

@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[str, ...] = ()

    def raise_if_invalid(self):
        if not self.valid:
            raise ArtifactValidationError("; ".join(self.errors))
        return self

class ArtifactValidationError(ValueError):
    """Raised when a report or artifact violates DI-1 invariants."""
