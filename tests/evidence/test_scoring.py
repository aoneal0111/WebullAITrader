from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.evidence import (
    Evidence,
    EvidenceCategory,
    EvidenceValidationError,
    SignalDirection,
    score_evidence,
)


def evidence(
    source: str,
    direction: SignalDirection,
    confidence: float,
    strength: float = 1.0,
    symbol: str = "AAPL",
) -> Evidence:
    return Evidence(
        symbol=symbol,
        timestamp=datetime.now(timezone.utc),
        source=source,
        category=EvidenceCategory.TECHNICAL,
        direction=direction,
        confidence=confidence,
        strength=strength,
        explanation=f"{source} test evidence.",
    )


def test_bullish_evidence_scores_long() -> None:
    result = score_evidence(
        [
            evidence("RSI", SignalDirection.LONG, 0.8),
            evidence("MACD", SignalDirection.LONG, 0.7),
        ]
    )

    assert result.direction is SignalDirection.LONG
    assert result.score == pytest.approx(0.75)
    assert result.bullish_count == 2


def test_bearish_evidence_scores_short() -> None:
    result = score_evidence(
        [
            evidence("RSI", SignalDirection.SHORT, 0.9),
            evidence("MACD", SignalDirection.SHORT, 0.7),
        ]
    )

    assert result.direction is SignalDirection.SHORT
    assert result.score == pytest.approx(-0.8)
    assert result.bearish_count == 2


def test_balanced_evidence_scores_neutral() -> None:
    result = score_evidence(
        [
            evidence("RSI", SignalDirection.LONG, 0.8),
            evidence("MACD", SignalDirection.SHORT, 0.8),
        ]
    )

    assert result.direction is SignalDirection.NEUTRAL
    assert result.score == pytest.approx(0.0)


def test_source_weights_affect_result() -> None:
    result = score_evidence(
        [
            evidence("RSI", SignalDirection.LONG, 0.6),
            evidence("VWAP", SignalDirection.SHORT, 0.8),
        ],
        source_weights={
            "RSI": 4.0,
            "VWAP": 1.0,
        },
    )

    assert result.direction is SignalDirection.LONG


def test_multiple_symbols_are_rejected() -> None:
    with pytest.raises(
        EvidenceValidationError,
        match="same symbol",
    ):
        score_evidence(
            [
                evidence(
                    "RSI",
                    SignalDirection.LONG,
                    0.8,
                    symbol="AAPL",
                ),
                evidence(
                    "MACD",
                    SignalDirection.LONG,
                    0.8,
                    symbol="MSFT",
                ),
            ]
        )


def test_empty_evidence_is_rejected() -> None:
    with pytest.raises(
        EvidenceValidationError,
        match="At least one",
    ):
        score_evidence([])
