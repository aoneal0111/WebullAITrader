"""Typed constants and validation models for DI-1 and DI-2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

ARTIFACT_SCHEMA_VERSION = "ATLAS_HISTORICAL_DECISION_INTELLIGENCE_V1"
COMPONENT_VERSIONS = (
    "ATLAS_PROFIT_RESEARCH_V1", "ATLAS_REENTRY_TRANSITIONS_V1",
    "ATLAS_PIT_PROFITABILITY_CONTEXT_V1", "ATLAS_BENCHMARK_REGIME_V1",
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


DI2_VERSION = "ATLAS_HISTORICAL_DECISION_INTELLIGENCE_DI2_V1"
ARTIFACT_VERSION = "ATLAS_HISTORICAL_DECISION_INTELLIGENCE_V1"


@dataclass(frozen=True, slots=True)
class HistoricalIntelligenceResult:
    artifact_version: str = ARTIFACT_VERSION
    recognized_memberships: tuple[str, ...] = ()
    primary_strategy: str | None = None
    membership_signature: str = ""
    opportunity_id: str | None = None
    trading_date: str | None = None
    session: str | None = None
    first_recognized_at: datetime | None = None
    opportunity_age_seconds: Decimal | None = None
    recognition_timing: str = "INSUFFICIENT_DATA"
    waiting_classification: str = "INSUFFICIENT_DATA"
    root_diagnostic: str = "INSUFFICIENT_DATA"
    recognition_source: str = "UNKNOWN"
    legacy_first_seen: datetime | None = None
    legacy_first_price: Decimal | None = None
    taxonomy_first_seen: datetime | None = None
    taxonomy_first_price: Decimal | None = None
    time_advantage_ms: Decimal | None = None
    price_advantage_percent: Decimal | None = None
    entry_decision_observed: bool = False
    setup_state: str = "NO_SETUP"
    setup_stage: str = "NO_SETUP"
    entry_location: str = "INSUFFICIENT_DATA"
    entry_assessment: str = "INSUFFICIENT_EVIDENCE"
    trigger_price: Decimal | None = None
    structural_stop: Decimal | None = None
    reference_price: Decimal | None = None
    current_price: Decimal | None = None
    gap_percent: Decimal | None = None
    relative_volume: Decimal | None = None
    volume_acceleration: Decimal | None = None
    distance_from_hod_percent: Decimal | None = None
    distance_to_trigger: Decimal | None = None
    distance_to_trigger_percent: Decimal | None = None
    price_beyond_trigger: Decimal | None = None
    price_beyond_trigger_percent: Decimal | None = None
    setup_evidence: tuple[dict[str, object], ...] = ()
    context_evidence: tuple[dict[str, object], ...] = ()
    failure_evidence: tuple[dict[str, object], ...] = ()
    confidence: str = "MISSING_CONTEXT"
    coverage: str = "MISSING_CONTEXT"
    limitations: tuple[str, ...] = ()
    fallback_reason: str | None = None
    evaluated_at: datetime | None = None
