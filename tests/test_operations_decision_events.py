from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.operations_core import DecisionsUpdated, OperationsDecision


NOW = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)


def make_decision() -> OperationsDecision:
    return OperationsDecision(
        symbol="AAPL",
        action="HOLD",
        confidence=50,
        score=Decimal("0.5"),
        reasons=(),
        source_action="HOLD",
        position_quantity=Decimal("0"),
        strategy_version="1.0",
        decided_at=NOW,
    )


def test_decisions_updated_accepts_an_immutable_batch() -> None:
    event = DecisionsUpdated(
        cycle=1,
        decisions=(make_decision(),),
        occurred_at=NOW,
    )

    assert event.cycle == 1
    assert event.decisions[0].symbol == "AAPL"


def test_decisions_updated_rejects_mutable_collection() -> None:
    with pytest.raises(TypeError, match="immutable tuple"):
        DecisionsUpdated(  # type: ignore[arg-type]
            cycle=1,
            decisions=[make_decision()],
            occurred_at=NOW,
        )
