from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Mapping

from app.evidence.enums import SignalDirection
from app.evidence.exceptions import EvidenceValidationError
from app.evidence.models import Evidence


@dataclass(frozen=True, slots=True)
class EvidenceScore:
    symbol: str
    score: float
    confidence: float
    direction: SignalDirection
    evidence_count: int
    bullish_count: int
    bearish_count: int
    neutral_count: int


def score_evidence(
    evidence: Iterable[Evidence],
    *,
    source_weights: Mapping[str, float] | None = None,
    neutral_threshold: float = 0.05,
) -> EvidenceScore:
    items = tuple(evidence)

    if not items:
        raise EvidenceValidationError(
            "At least one evidence item is required"
        )

    symbols = {item.symbol for item in items}

    if len(symbols) != 1:
        raise EvidenceValidationError(
            "All evidence items must use the same symbol"
        )

    threshold = _validate_threshold(neutral_threshold)
    weights = _normalize_weights(source_weights or {})

    weighted_score = 0.0
    total_weight = 0.0
    confidence_total = 0.0

    bullish_count = 0
    bearish_count = 0
    neutral_count = 0

    for item in items:
        source_weight = weights.get(item.source, 1.0)
        contribution_weight = source_weight * item.strength

        weighted_score += (
            item.direction.polarity
            * item.confidence
            * contribution_weight
        )
        total_weight += contribution_weight
        confidence_total += item.confidence * contribution_weight

        if item.direction.polarity > 0:
            bullish_count += 1
        elif item.direction.polarity < 0:
            bearish_count += 1
        else:
            neutral_count += 1

    if total_weight == 0:
        normalized_score = 0.0
        normalized_confidence = 0.0
    else:
        normalized_score = weighted_score / total_weight
        normalized_confidence = confidence_total / total_weight

    if normalized_score > threshold:
        direction = SignalDirection.LONG
    elif normalized_score < -threshold:
        direction = SignalDirection.SHORT
    else:
        direction = SignalDirection.NEUTRAL

    return EvidenceScore(
        symbol=items[0].symbol,
        score=normalized_score,
        confidence=normalized_confidence,
        direction=direction,
        evidence_count=len(items),
        bullish_count=bullish_count,
        bearish_count=bearish_count,
        neutral_count=neutral_count,
    )


def _normalize_weights(
    weights: Mapping[str, float],
) -> dict[str, float]:
    normalized: dict[str, float] = {}

    for source, weight in weights.items():
        if not isinstance(source, str) or not source.strip():
            raise EvidenceValidationError(
                "Source weight names must be nonblank strings"
            )

        if isinstance(weight, bool):
            raise EvidenceValidationError(
                "Source weights must be numeric"
            )

        try:
            numeric_weight = float(weight)
        except (TypeError, ValueError) as exc:
            raise EvidenceValidationError(
                "Source weights must be numeric"
            ) from exc

        if not math.isfinite(numeric_weight):
            raise EvidenceValidationError(
                "Source weights must be finite"
            )

        if numeric_weight < 0:
            raise EvidenceValidationError(
                "Source weights cannot be negative"
            )

        normalized[source.strip()] = numeric_weight

    return normalized


def _validate_threshold(value: float) -> float:
    try:
        threshold = float(value)
    except (TypeError, ValueError) as exc:
        raise EvidenceValidationError(
            "neutral_threshold must be numeric"
        ) from exc

    if not math.isfinite(threshold):
        raise EvidenceValidationError(
            "neutral_threshold must be finite"
        )

    if not 0.0 <= threshold <= 1.0:
        raise EvidenceValidationError(
            "neutral_threshold must be between 0 and 1"
        )

    return threshold
