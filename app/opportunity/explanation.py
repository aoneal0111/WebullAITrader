from __future__ import annotations

from .models import OpportunityAssessment


def build_explanation(
    assessment: OpportunityAssessment,
) -> tuple[str, ...]:
    lines = [
        f"Opportunity Score: {assessment.opportunity_score:.1f}",
        "",
        "Passed Rules:",
    ]

    lines.extend(assessment.scanner.passed_rules)

    if assessment.scanner.failed_rules:
        lines.append("")
        lines.append("Failed Rules:")
        lines.extend(assessment.scanner.failed_rules)

    return tuple(lines)
