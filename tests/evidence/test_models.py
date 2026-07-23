from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from types import MappingProxyType

import pytest

from app.evidence import (
    Evidence,
    EvidenceCategory,
    EvidenceValidationError,
    SignalDirection,
)


def make_evidence(**overrides: object) -> Evidence:
    values: dict[str, object] = {
        "symbol": "aapl",
        "timestamp": datetime(
            2026,
            7,
            21,
            15,
            30,
            tzinfo=timezone.utc,
        ),
        "source": "RSI",
        "category": EvidenceCategory.TECHNICAL,
        "direction": SignalDirection.LONG,
        "confidence": 0.8,
        "strength": 0.75,
        "explanation": "RSI recovered from oversold conditions.",
        "features": {
            "rsi": 31.2,
            "period": 14,
            "conditions": ["oversold", "recovering"],
        },
        "metadata": {"version": "1"},
    }
    values.update(overrides)
    return Evidence(**values)  # type: ignore[arg-type]


def test_evidence_normalizes_values() -> None:
    evidence = make_evidence(
        symbol=" aapl ",
        source=" RSI ",
        explanation=" Signal confirmed. ",
    )

    assert evidence.symbol == "AAPL"
    assert evidence.source == "RSI"
    assert evidence.explanation == "Signal confirmed."


def test_evidence_is_frozen() -> None:
    evidence = make_evidence()

    with pytest.raises(FrozenInstanceError):
        evidence.symbol = "MSFT"  # type: ignore[misc]


def test_feature_mappings_are_immutable() -> None:
    evidence = make_evidence()

    assert isinstance(evidence.features, MappingProxyType)

    with pytest.raises(TypeError):
        evidence.features["rsi"] = 50  # type: ignore[index]


def test_nested_values_are_frozen() -> None:
    evidence = make_evidence()

    assert evidence.features["conditions"] == (
        "oversold",
        "recovering",
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("symbol", " ", "symbol cannot be blank"),
        ("source", "", "source cannot be blank"),
        (
            "explanation",
            " ",
            "explanation cannot be blank",
        ),
        (
            "confidence",
            1.01,
            "confidence must be between 0 and 1",
        ),
        (
            "strength",
            -0.01,
            "strength must be between 0 and 1",
        ),
    ],
)
def test_invalid_values_are_rejected(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(EvidenceValidationError, match=message):
        make_evidence(**{field: value})


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(
        EvidenceValidationError,
        match="timezone-aware",
    ):
        make_evidence(timestamp=datetime(2026, 7, 21, 15, 30))


def test_unsupported_feature_value_is_rejected() -> None:
    with pytest.raises(
        EvidenceValidationError,
        match="unsupported value type",
    ):
        make_evidence(features={"bad": object()})


def test_directional_score_is_positive_for_long() -> None:
    evidence = make_evidence(
        confidence=0.8,
        strength=0.5,
        direction=SignalDirection.LONG,
    )

    assert evidence.directional_score == pytest.approx(0.4)


def test_directional_score_is_negative_for_short() -> None:
    evidence = make_evidence(
        confidence=0.8,
        strength=0.5,
        direction=SignalDirection.SHORT,
    )

    assert evidence.directional_score == pytest.approx(-0.4)


def test_serialization_round_trip() -> None:
    original = make_evidence()

    restored = Evidence.from_dict(original.to_dict())

    assert restored == original
    assert restored.to_dict() == original.to_dict()
