from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from types import MappingProxyType

import pytest

from app.committee import (
    AgentOpinion,
    AgentOpinionAction,
    TechnicalAgentAction,
    TechnicalAgentOpinion,
)


NOW = datetime(2026, 7, 21, 18, tzinfo=UTC)


def opinion(**overrides: object) -> AgentOpinion:
    values: dict[str, object] = {
        "agent_name": "technical_agent_v1",
        "symbol": "AAPL",
        "timestamp": NOW,
        "action": AgentOpinionAction.BULLISH,
        "confidence": 0.8,
        "score": 0.6,
        "reasons": ("Technical evidence was bullish.",),
        "evidence_ids": ("evidence-1",),
        "metadata": {"nested": {"values": [1, 2]}},
    }
    values.update(overrides)
    return AgentOpinion(**values)  # type: ignore[arg-type]


def technical_opinion(action: TechnicalAgentAction) -> TechnicalAgentOpinion:
    score = {
        TechnicalAgentAction.BULLISH: 0.6,
        TechnicalAgentAction.BEARISH: -0.6,
        TechnicalAgentAction.NEUTRAL: 0.1,
    }[action]
    return TechnicalAgentOpinion(
        symbol="AAPL",
        timestamp=NOW,
        action=action,
        confidence=0.75,
        score=score,
        bullish_count=int(action is TechnicalAgentAction.BULLISH),
        bearish_count=int(action is TechnicalAgentAction.BEARISH),
        neutral_count=int(action is TechnicalAgentAction.NEUTRAL),
        evidence_ids=("id-1",),
        reasons=("Technical agent reason.",),
    )


def test_agent_opinion_is_frozen_and_metadata_is_defensively_frozen() -> None:
    metadata = {"nested": {"values": [1, 2]}}
    item = opinion(metadata=metadata)
    metadata["new"] = True
    assert isinstance(item.metadata, MappingProxyType)
    assert isinstance(item.metadata["nested"], MappingProxyType)
    assert item.metadata["nested"]["values"] == (1, 2)  # type: ignore[index]
    assert "new" not in item.metadata
    with pytest.raises(FrozenInstanceError):
        item.score = 0  # type: ignore[misc]


def test_symbol_must_already_be_uppercase_and_is_stripped() -> None:
    assert opinion(symbol=" AAPL ").symbol == "AAPL"
    with pytest.raises(ValueError, match="uppercase"):
        opinion(symbol="aapl")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("timestamp", datetime(2026, 7, 21), "timezone-aware"),
        ("confidence", -0.01, "between"),
        ("confidence", 1.01, "between"),
        ("score", -1.01, "between"),
        ("score", 1.01, "between"),
        ("confidence", float("nan"), "finite"),
        ("score", float("inf"), "finite"),
        ("confidence", True, "boolean"),
        ("score", False, "boolean"),
        ("agent_name", " ", "nonempty"),
        ("reasons", ("",), "nonempty"),
        ("evidence_ids", ("same", "same"), "unique"),
    ],
)
def test_invalid_agent_opinion_fields(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        opinion(**{field: value})


def test_action_and_score_relationship_is_validated() -> None:
    with pytest.raises(ValueError, match="BULLISH"):
        opinion(score=-0.1)
    with pytest.raises(ValueError, match="BEARISH"):
        opinion(action=AgentOpinionAction.BEARISH, score=0.1)
    assert opinion(action=AgentOpinionAction.NEUTRAL, score=0.1).score == 0.1


@pytest.mark.parametrize(
    ("technical_action", "normalized_action"),
    [
        (TechnicalAgentAction.BULLISH, AgentOpinionAction.BULLISH),
        (TechnicalAgentAction.BEARISH, AgentOpinionAction.BEARISH),
        (TechnicalAgentAction.NEUTRAL, AgentOpinionAction.NEUTRAL),
    ],
)
def test_technical_adapter_uses_explicit_action_mapping(
    technical_action: TechnicalAgentAction,
    normalized_action: AgentOpinionAction,
) -> None:
    original = technical_opinion(technical_action)
    before = replace(original)
    adapted = AgentOpinion.from_technical_opinion(original)
    assert adapted.agent_name == "technical_agent_v1"
    assert adapted.symbol == original.symbol
    assert adapted.timestamp == original.timestamp
    assert adapted.action is normalized_action
    assert adapted.confidence == original.confidence
    assert adapted.score == original.score
    assert adapted.reasons == original.reasons
    assert adapted.evidence_ids == original.evidence_ids
    assert adapted.metadata == {
        "specialist_type": "technical",
        "adapter_version": "1",
        "deterministic": True,
    }
    assert original == before


def test_agent_opinion_serialization_round_trip_is_json_safe() -> None:
    original = opinion()
    serialized = original.to_dict()
    json.dumps(serialized, allow_nan=False)
    restored = AgentOpinion.from_dict(serialized)
    assert restored == original
