from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.committee import AgentOpinion, CommitteeChair
from app.evidence.providers import (
    TechnicalSnapshotEvidenceProvider,
    TechnicalSnapshotInput,
)
from app.indicators.market_snapshot import MarketSnapshot
from app.committee import TechnicalAgent


OBSERVED = datetime(2026, 7, 21, 19, tzinfo=UTC)
EVALUATED = datetime(2026, 7, 21, 19, 1, tzinfo=UTC)


def test_analysis_only_technical_to_committee_pipeline() -> None:
    snapshot = MarketSnapshot(
        symbol="AAPL",
        close=Decimal("100"),
        ema_12=Decimal("102"),
        ema_26=Decimal("98"),
        rsi_14=Decimal("25"),
        macd=Decimal("1"),
        macd_signal=Decimal("0.5"),
        macd_histogram=Decimal("0.5"),
        atr_14=Decimal("2"),
        bollinger_upper=Decimal("110"),
        bollinger_middle=Decimal("100"),
        bollinger_lower=Decimal("90"),
        vwap=Decimal("98"),
    )
    evidence = TechnicalSnapshotEvidenceProvider().generate(
        TechnicalSnapshotInput(snapshot, OBSERVED)
    )
    technical = TechnicalAgent().evaluate(evidence, timestamp=OBSERVED)
    normalized = AgentOpinion.from_technical_opinion(technical)
    committee = CommitteeChair().evaluate(
        (normalized,), timestamp=EVALUATED
    )

    assert committee.symbol == "AAPL"
    assert normalized.timestamp == OBSERVED
    assert committee.timestamp == EVALUATED
    assert committee.agent_names == ("technical_agent_v1",)
    assert committee.votes[0].agent_name == "technical_agent_v1"
    assert committee.metadata["deterministic"] is True
    assert not hasattr(committee, "side")
    assert not hasattr(committee, "quantity")
