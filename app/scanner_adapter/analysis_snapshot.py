from __future__ import annotations

from dataclasses import dataclass

from app.committee import CommitteeOpinion
from app.momentum_scanner.models import ScannerDecision
from app.opportunity import OpportunityAssessment


@dataclass(frozen=True, slots=True)
class RankedAnalysis:
    """Immutable aggregate for a fully evaluated scanner candidate."""

    symbol: str
    decision: ScannerDecision
    assessment: OpportunityAssessment
    committee: CommitteeOpinion
