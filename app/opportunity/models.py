from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from app.momentum_scanner.models import ScannerDecision


@dataclass(frozen=True, slots=True)
class DecisionMetadata:
    assessment_id: str = field(default_factory=lambda: str(uuid4()))
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    engine_version: str = "1.0.0"
    model_version: str = "1.0.0"
    profile_name: str = "research"


@dataclass(frozen=True, slots=True)
class NormalizedScore:
    score: float
    confidence: float
    explanation: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OpportunityAssessment:
    metadata: DecisionMetadata
    scanner: ScannerDecision

    scanner_score: NormalizedScore

    committee_score: NormalizedScore | None = None
    technical_score: NormalizedScore | None = None
    regime_score: NormalizedScore | None = None
    risk_score: NormalizedScore | None = None
    portfolio_score: NormalizedScore | None = None
    execution_score: NormalizedScore | None = None

    opportunity_score: float = 0.0

    explanation: tuple[str, ...] = ()
