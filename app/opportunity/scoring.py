from __future__ import annotations

from app.momentum_scanner.models import ScannerDecision

from .models import NormalizedScore


def scanner_score(decision: ScannerDecision) -> NormalizedScore:
    return NormalizedScore(
        score=float(decision.score),
        confidence=100.0 if decision.qualified else 0.0,
        explanation=(
            f"Scanner score: {decision.score}",
            *decision.passed_rules,
        ),
    )
