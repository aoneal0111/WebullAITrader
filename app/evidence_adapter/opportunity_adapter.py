from __future__ import annotations

from app.evidence.enums import EvidenceCategory, SignalDirection
from app.evidence.models import Evidence
from app.opportunity.models import OpportunityAssessment


def opportunity_to_evidence(
    assessment: OpportunityAssessment,
) -> tuple[Evidence, ...]:

    score = assessment.scanner_score.score / 100.0

    if score >= 0.60:
        direction = SignalDirection.LONG
    elif score <= 0.40:
        direction = SignalDirection.SHORT
    else:
        direction = SignalDirection.NEUTRAL

    evidence = Evidence(
        symbol=assessment.scanner.symbol,
        timestamp=assessment.metadata.generated_at,
        source="opportunity_engine_v1",
        category=EvidenceCategory.MOMENTUM,
        direction=direction,
        confidence=min(
            assessment.scanner_score.confidence / 100.0,
            1.0,
        ),
        strength=min(score, 1.0),
        explanation="Opportunity Engine assessment",
        features={
            "scanner_score": assessment.scanner_score.score,
            "opportunity_score": assessment.opportunity_score,
        },
        metadata={
            "assessment_id": assessment.metadata.assessment_id,
            "engine_version": assessment.metadata.engine_version,
        },
    )

    return (evidence,)
