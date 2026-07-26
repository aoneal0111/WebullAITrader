from __future__ import annotations

from dataclasses import replace

from app.momentum_scanner.models import ScannerDecision

from .explanation import build_explanation
from .models import DecisionMetadata, OpportunityAssessment
from .scoring import scanner_score


class OpportunityEngine:
    def evaluate(
        self,
        decision: ScannerDecision,
    ) -> OpportunityAssessment:
        score = scanner_score(decision)

        assessment = OpportunityAssessment(
            metadata=DecisionMetadata(),
            scanner=decision,
            scanner_score=score,
            opportunity_score=score.score,
        )

        return replace(
            assessment,
            explanation=build_explanation(assessment),
        )
