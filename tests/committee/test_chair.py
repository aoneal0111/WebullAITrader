from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType

import pytest

from app.committee import (
    AgentOpinion,
    AgentOpinionAction,
    AgentWeightConfiguration,
    CommitteeAction,
    CommitteeChair,
    TechnicalAgentAction,
    TechnicalAgentOpinion,
)


NOW = datetime(2026, 7, 21, 19, tzinfo=UTC)


def opinion(
    name: str,
    action: AgentOpinionAction,
    score: float,
    *,
    confidence: float = 1.0,
    symbol: str = "AAPL",
    timestamp: datetime = NOW,
) -> AgentOpinion:
    return AgentOpinion(
        agent_name=name,
        symbol=symbol,
        timestamp=timestamp,
        action=action,
        confidence=confidence,
        score=score,
        reasons=(f"{name} observed {action.value.lower()} conditions.",),
    )


def test_chair_accepts_only_normalized_agent_opinions() -> None:
    technical = TechnicalAgentOpinion(
        symbol="AAPL",
        timestamp=NOW,
        action=TechnicalAgentAction.BULLISH,
        confidence=0.8,
        score=0.6,
        bullish_count=1,
        bearish_count=0,
        neutral_count=0,
        evidence_ids=("id",),
        reasons=("reason",),
    )
    with pytest.raises(ValueError, match="AgentOpinion"):
        CommitteeChair().evaluate((technical,), timestamp=NOW)  # type: ignore[arg-type]


def test_no_opinions_returns_safe_neutral() -> None:
    result = CommitteeChair().evaluate((), timestamp=NOW)
    assert result.symbol == "UNKNOWN"
    assert result.action is CommitteeAction.NEUTRAL
    assert result.score == result.confidence == result.consensus == 0
    assert result.participating_agents == 0
    assert result.votes == ()
    assert result.reasons[0] == (
        "No specialist opinions met committee inclusion requirements."
    )


@pytest.mark.parametrize(
    ("action", "score", "expected"),
    [
        (AgentOpinionAction.BULLISH, 0.2, CommitteeAction.BULLISH),
        (AgentOpinionAction.BEARISH, -0.2, CommitteeAction.BEARISH),
        (AgentOpinionAction.NEUTRAL, 0.19, CommitteeAction.NEUTRAL),
    ],
)
def test_single_opinion_thresholds(
    action: AgentOpinionAction,
    score: float,
    expected: CommitteeAction,
) -> None:
    result = CommitteeChair().evaluate(
        (opinion("agent", action, score),), timestamp=NOW
    )
    assert result.action is expected
    assert result.score == score
    assert result.consensus == 1


@pytest.mark.parametrize(
    ("action", "score", "expected_score"),
    [
        (AgentOpinionAction.BULLISH, 0.8, 0.7),
        (AgentOpinionAction.BEARISH, -0.8, -0.7),
    ],
)
def test_multiple_directional_opinions_aggregate(
    action: AgentOpinionAction,
    score: float,
    expected_score: float,
) -> None:
    result = CommitteeChair().evaluate(
        (
            opinion("agent_b", action, score),
            opinion(
                "agent_a",
                action,
                2 * expected_score - score,
            ),
        ),
        timestamp=NOW,
    )
    assert result.score == pytest.approx(expected_score)
    assert result.consensus == 1


def test_equal_opposing_votes_cancel_with_confidence_cap() -> None:
    result = CommitteeChair().evaluate(
        (
            opinion("bull", AgentOpinionAction.BULLISH, 0.8),
            opinion("bear", AgentOpinionAction.BEARISH, -0.8),
        ),
        timestamp=NOW,
    )
    assert result.action is CommitteeAction.NEUTRAL
    assert result.score == 0
    assert result.consensus == 0.5
    assert result.confidence <= 0.5
    assert result.reasons[0] == (
        "Opposing specialist opinions produced a NEUTRAL result."
    )


def test_configured_weight_and_confidence_change_effective_vote() -> None:
    configuration = AgentWeightConfiguration(
        weights={"bull": 1.0, "bear": 0.5}
    )
    result = CommitteeChair(configuration).evaluate(
        (
            opinion("bull", AgentOpinionAction.BULLISH, 1, confidence=0.5),
            opinion("bear", AgentOpinionAction.BEARISH, -1, confidence=0.5),
        ),
        timestamp=NOW,
    )
    assert result.score == pytest.approx(1 / 3)
    assert result.votes[0].effective_weight == 0.25
    assert result.votes[1].effective_weight == 0.5
    assert result.votes[0].weighted_score == -0.25
    assert result.votes[1].weighted_score == 0.5


def test_unknown_agent_uses_default_weight() -> None:
    result = CommitteeChair(
        AgentWeightConfiguration(weights={}, default_weight=0.4)
    ).evaluate(
        (opinion("unknown", AgentOpinionAction.BULLISH, 0.5),),
        timestamp=NOW,
    )
    assert result.votes[0].configured_weight == 0.4


@pytest.mark.parametrize(
    ("configuration", "reason"),
    [
        (
            AgentWeightConfiguration(weights={"agent": 0}),
            "Configured weight is zero.",
        ),
        (
            AgentWeightConfiguration(minimum_confidence=0.8),
            "Opinion confidence is below the configured minimum.",
        ),
    ],
)
def test_excluded_votes_are_audited_without_affecting_aggregation(
    configuration: AgentWeightConfiguration,
    reason: str,
) -> None:
    result = CommitteeChair(configuration).evaluate(
        (
            opinion(
                "agent",
                AgentOpinionAction.BULLISH,
                1,
                confidence=0.5,
            ),
        ),
        timestamp=NOW,
    )
    assert result.participating_agents == 0
    assert result.bullish_agents == 0
    assert result.score == result.confidence == result.consensus == 0
    assert result.votes[0].included is False
    assert result.votes[0].effective_weight == 0
    assert result.votes[0].weighted_score == 0
    assert result.votes[0].exclusion_reason == reason


def test_mixed_symbols_and_duplicate_agents_are_rejected() -> None:
    with pytest.raises(ValueError, match="mixed symbols"):
        CommitteeChair().evaluate(
            (
                opinion("a", AgentOpinionAction.BULLISH, 0.5),
                opinion("b", AgentOpinionAction.BULLISH, 0.5, symbol="MSFT"),
            ),
            timestamp=NOW,
        )
    with pytest.raises(ValueError, match="duplicate agent"):
        CommitteeChair().evaluate(
            (
                opinion("a", AgentOpinionAction.BULLISH, 0.5),
                opinion("a", AgentOpinionAction.BULLISH, 0.5),
            ),
            timestamp=NOW,
        )


def test_timestamps_are_validated_only_against_supplied_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        CommitteeChair().evaluate((), timestamp=datetime(2026, 7, 21))
    with pytest.raises(ValueError, match="later"):
        CommitteeChair().evaluate(
            (
                opinion(
                    "agent",
                    AgentOpinionAction.BULLISH,
                    0.5,
                    timestamp=NOW + timedelta(seconds=1),
                ),
            ),
            timestamp=NOW,
        )


def test_ordering_reasons_and_values_are_deterministic() -> None:
    first = opinion("first", AgentOpinionAction.BULLISH, 0.8)
    second = opinion("second", AgentOpinionAction.BEARISH, -0.4)
    chair = CommitteeChair()
    forward = chair.evaluate((first, second), timestamp=NOW)
    reverse = chair.evaluate((second, first), timestamp=NOW)
    assert forward == reverse
    assert forward.agent_names == ("first", "second")
    assert tuple(vote.agent_name for vote in forward.votes) == (
        "first",
        "second",
    )
    assert forward.reasons[1].startswith("first:")
    assert forward.reasons[2].startswith("second:")


def test_three_way_equal_consensus_and_counts() -> None:
    result = CommitteeChair().evaluate(
        (
            opinion("bull", AgentOpinionAction.BULLISH, 0.6),
            opinion("bear", AgentOpinionAction.BEARISH, -0.6),
            opinion("neutral", AgentOpinionAction.NEUTRAL, 0),
        ),
        timestamp=NOW,
    )
    assert result.consensus == pytest.approx(1 / 3)
    assert result.participating_agents == 3
    assert (result.bullish_agents, result.bearish_agents, result.neutral_agents) == (1, 1, 1)
    assert 0 <= result.confidence <= 0.5
    assert -1 <= result.score <= 1


def test_neutral_confidence_cap_is_enforced() -> None:
    result = CommitteeChair().evaluate(
        (opinion("neutral", AgentOpinionAction.NEUTRAL, 0.19),),
        timestamp=NOW,
    )
    assert result.action is CommitteeAction.NEUTRAL
    assert result.confidence <= 0.7


def test_metadata_and_serialization_are_immutable_and_json_safe() -> None:
    result = CommitteeChair().evaluate(
        (opinion("agent", AgentOpinionAction.BULLISH, 0.5),),
        timestamp=NOW,
    )
    assert isinstance(result.metadata, MappingProxyType)
    assert result.metadata["deterministic"] is True
    assert result.metadata["excluded_opinions"] == 0
    json.dumps(result.to_dict(), allow_nan=False)
    with pytest.raises(TypeError):
        result.metadata["deterministic"] = False  # type: ignore[index]


def test_chair_has_no_forbidden_dependencies_or_execution_actions() -> None:
    tree = ast.parse(Path("app/committee/chair.py").read_text(encoding="utf-8"))
    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    forbidden = (
        "evidence",
        "indicator",
        "snapshot",
        "broker",
        "risk",
        "execution",
        "market_data",
        "openai",
        "network",
    )
    assert not any(term in imported for imported in imports for term in forbidden)
    assert {action.value for action in CommitteeAction} == {
        "BULLISH",
        "BEARISH",
        "NEUTRAL",
    }
    source = Path("app/committee/chair.py").read_text(encoding="utf-8")
    assert "OrderIntent" not in source
    assert "BUY" not in source
    assert "SELL" not in source
