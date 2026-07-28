from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.read_models.decisions import DecisionReadModel, DecisionsReadModelSnapshot


NOW = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)


def make_decision() -> DecisionReadModel:
    return DecisionReadModel(
        symbol="AAPL",
        action="ENTER_LONG",
        confidence=82,
        score=Decimal("0.82"),
        reasons=("momentum confirmed",),
        source_action="BUY",
        position_quantity=Decimal("0"),
        strategy_version="1.0",
        decided_at=NOW,
    )


def test_decision_read_model_is_frozen_and_slotted() -> None:
    decision = make_decision()

    with pytest.raises(FrozenInstanceError):
        decision.action = "HOLD"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        decision.extra = "mutable"  # type: ignore[attr-defined]


def test_snapshot_requires_immutable_decisions() -> None:
    with pytest.raises(TypeError, match="immutable tuple"):
        DecisionsReadModelSnapshot(  # type: ignore[arg-type]
            cycle=1,
            updated_at=NOW,
            decisions=[make_decision()],
        )


def test_initial_snapshot_has_no_cycle_or_decisions() -> None:
    assert DecisionsReadModelSnapshot.initial() == DecisionsReadModelSnapshot()


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"confidence": 101}, "confidence"),
        ({"symbol": "aapl"}, "uppercase"),
        ({"reasons": ("",)}, "reasons"),
        ({"decided_at": NOW.replace(tzinfo=None)}, "timezone-aware"),
    ),
)
def test_decision_read_model_rejects_invalid_values(changes, message) -> None:
    values = {
        "symbol": "AAPL",
        "action": "HOLD",
        "confidence": 50,
        "score": Decimal("0.5"),
        "reasons": (),
        "source_action": "HOLD",
        "position_quantity": Decimal("0"),
        "strategy_version": "1.0",
        "decided_at": NOW,
    }
    values.update(changes)

    with pytest.raises(ValueError, match=message):
        DecisionReadModel(**values)
