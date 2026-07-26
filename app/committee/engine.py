from __future__ import annotations

from app.committee import (
    AgentOpinion,
    CommitteeChair,
    CommitteeOpinion,
    TechnicalAgent,
)
from app.evidence_adapter import opportunity_to_evidence
from app.opportunity import OpportunityAssessment


class CommitteeEngine:
    def __init__(self) -> None:
        self._technical = TechnicalAgent()
        self._chair = CommitteeChair()

    def evaluate(
        self,
        assessment: OpportunityAssessment,
    ) -> CommitteeOpinion:

        evidence = opportunity_to_evidence(
            assessment,
        )

        technical = self._technical.evaluate(
            evidence,
            timestamp=assessment.metadata.generated_at,
        )

        opinion = AgentOpinion.from_technical_opinion(
            technical,
        )

        return self._chair.evaluate(
            (opinion,),
            timestamp=assessment.metadata.generated_at,
        )
