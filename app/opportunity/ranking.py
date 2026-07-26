from __future__ import annotations

from collections.abc import Iterable

from .models import OpportunityAssessment


def rank_opportunities(
    assessments: Iterable[OpportunityAssessment],
    *,
    limit: int = 25,
) -> tuple[OpportunityAssessment, ...]:
    ranked = sorted(
        assessments,
        key=lambda a: (
            a.opportunity_score,
            a.scanner_score.score,
            a.scanner_score.confidence,
        ),
        reverse=True,
    )

    return tuple(ranked[:limit])
