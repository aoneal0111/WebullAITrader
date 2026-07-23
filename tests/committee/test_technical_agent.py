from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from app.committee import TechnicalAgent, TechnicalAgentAction
from app.evidence import Evidence, EvidenceCategory, SignalDirection


NOW = datetime(2026, 7, 21, 16, tzinfo=UTC)
SOURCE = "technical_snapshot_v1"


def evidence(
    indicator: str,
    direction: SignalDirection,
    confidence: float = 0.8,
    strength: float = 1.0,
    *,
    symbol: str = "AAPL",
    source: str = SOURCE,
    category: EvidenceCategory = EvidenceCategory.TECHNICAL,
    evidence_id: UUID | None = None,
    role: str | None = None,
) -> Evidence:
    values = dict(
        symbol=symbol,
        timestamp=NOW,
        source=source,
        category=category,
        direction=direction,
        confidence=confidence,
        strength=strength,
        explanation=f"{indicator} observed {direction.value} evidence.",
        metadata={
            "indicator": indicator,
            **({"role": role} if role is not None else {}),
        },
    )
    if evidence_id is not None:
        values["evidence_id"] = evidence_id
    return Evidence(**values)  # type: ignore[arg-type]


def test_no_evidence_returns_safe_neutral() -> None:
    result = TechnicalAgent().evaluate((), timestamp=NOW)
    assert result.action is TechnicalAgentAction.NEUTRAL
    assert result.symbol == "UNKNOWN"
    assert result.confidence == result.score == 0
    assert (result.bullish_count, result.bearish_count, result.neutral_count) == (0, 0, 0)
    assert result.evidence_ids == ()
    assert result.reasons == ("No usable technical evidence was supplied.",)


@pytest.mark.parametrize(
    ("direction", "expected"),
    [(SignalDirection.LONG, TechnicalAgentAction.BULLISH), (SignalDirection.SHORT, TechnicalAgentAction.BEARISH)],
)
def test_directional_consensus(direction: SignalDirection, expected: TechnicalAgentAction) -> None:
    result = TechnicalAgent().evaluate((evidence("ema", direction), evidence("macd", direction)), timestamp=NOW)
    assert result.action is expected
    assert 0 <= result.confidence <= 1
    assert abs(result.score) <= 1
    expected_counts = (2, 0, 0) if direction is SignalDirection.LONG else (0, 2, 0)
    assert (result.bullish_count, result.bearish_count, result.neutral_count) == expected_counts


def test_conflicting_evidence_within_threshold_is_neutral() -> None:
    result = TechnicalAgent().evaluate((evidence("ema", SignalDirection.LONG), evidence("macd", SignalDirection.SHORT)), timestamp=NOW)
    assert result.action is TechnicalAgentAction.NEUTRAL
    assert result.score == pytest.approx(0)
    assert (result.bullish_count, result.bearish_count, result.neutral_count) == (1, 1, 0)


def test_atr_context_does_not_dilute_directional_score() -> None:
    ema = evidence(
        "ema_cross",
        SignalDirection.LONG,
        confidence=0.8,
        strength=0.25,
    )
    atr = evidence(
        "atr_14",
        SignalDirection.NEUTRAL,
        confidence=0.8,
        strength=1.0,
        role="volatility_context",
    )
    agent = TechnicalAgent()

    without_atr = agent.evaluate((ema,), timestamp=NOW)
    with_atr = agent.evaluate((ema, atr), timestamp=NOW)

    assert with_atr.action is TechnicalAgentAction.BULLISH
    assert with_atr.score == without_atr.score
    assert with_atr.confidence == without_atr.confidence
    assert with_atr.neutral_count == 1
    assert str(atr.evidence_id) in with_atr.evidence_ids


def test_only_atr_context_returns_zero_confidence_neutral() -> None:
    atr = evidence(
        "atr_14",
        SignalDirection.NEUTRAL,
        role="volatility_context",
    )

    result = TechnicalAgent().evaluate((atr,), timestamp=NOW)

    assert result.action is TechnicalAgentAction.NEUTRAL
    assert result.score == 0
    assert result.confidence == 0
    assert result.neutral_count == 1


@pytest.mark.parametrize(
    ("indicator", "direction", "role"),
    [
        ("atr_14", SignalDirection.NEUTRAL, None),
        ("atr_14", SignalDirection.LONG, "volatility_context"),
        ("ema_cross", SignalDirection.NEUTRAL, "volatility_context"),
    ],
)
def test_malformed_volatility_context_is_rejected(
    indicator: str,
    direction: SignalDirection,
    role: str | None,
) -> None:
    with pytest.raises(ValueError, match="ATR|volatility_context"):
        TechnicalAgent().evaluate(
            (evidence(indicator, direction, role=role),),
            timestamp=NOW,
        )


def test_mixed_symbols_are_rejected() -> None:
    with pytest.raises(ValueError, match="mixed symbols"):
        TechnicalAgent().evaluate((evidence("ema", SignalDirection.LONG), evidence("macd", SignalDirection.LONG, symbol="MSFT")), timestamp=NOW)


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        TechnicalAgent().evaluate((), timestamp=datetime(2026, 7, 21))


def test_duplicate_evidence_ids_are_rejected() -> None:
    identifier = UUID(int=1)
    with pytest.raises(ValueError, match="duplicate"):
        TechnicalAgent().evaluate((evidence("ema", SignalDirection.LONG, evidence_id=identifier), evidence("macd", SignalDirection.LONG, evidence_id=identifier)), timestamp=NOW)


def test_nontechnical_evidence_is_rejected() -> None:
    with pytest.raises(ValueError, match="technical category"):
        TechnicalAgent().evaluate((evidence("news", SignalDirection.LONG, category=EvidenceCategory.NEWS),), timestamp=NOW)


def test_unexpected_source_is_rejected() -> None:
    with pytest.raises(ValueError, match=SOURCE):
        TechnicalAgent().evaluate((evidence("ema", SignalDirection.LONG, source="other"),), timestamp=NOW)


def test_ids_and_reasons_have_deterministic_indicator_order() -> None:
    macd = evidence("macd", SignalDirection.LONG, evidence_id=UUID(int=2))
    ema = evidence("ema_cross", SignalDirection.LONG, evidence_id=UUID(int=1))
    result = TechnicalAgent().evaluate((macd, ema), timestamp=NOW)
    assert result.evidence_ids == (str(ema.evidence_id), str(macd.evidence_id))
    assert result.reasons == (ema.explanation, macd.explanation)


def test_identical_semantic_input_order_produces_identical_opinions() -> None:
    ema = evidence("ema_cross", SignalDirection.LONG, evidence_id=UUID(int=1))
    macd = evidence("macd", SignalDirection.SHORT, confidence=0.6, evidence_id=UUID(int=2))
    agent = TechnicalAgent()
    assert agent.evaluate((ema, macd), timestamp=NOW) == agent.evaluate((macd, ema), timestamp=NOW)


def test_agent_module_has_only_analysis_dependencies() -> None:
    path = Path("app/committee/technical_agent.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    forbidden = ("market_snapshot", "indicator", "broker", "execution", "network", "requests", "httpx")
    assert not any(term in imported for imported in imports for term in forbidden)
