from datetime import datetime, timezone
from decimal import Decimal

from app.operations_core import (
    DecisionsUpdated,
    OperationsBus,
    OperationsDecision,
    RuntimeStarting,
)
from app.read_models.decisions import DecisionProjector


NOW = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)


def make_decision(symbol: str = "AAPL") -> OperationsDecision:
    return OperationsDecision(
        symbol=symbol,
        action="ENTER_LONG",
        confidence=82,
        score=Decimal("0.82"),
        reasons=("momentum confirmed", "risk approved"),
        source_action="BUY",
        position_quantity=Decimal("0"),
        strategy_version="1.0",
        decided_at=NOW,
    )


def test_projector_consumes_decision_events_into_immutable_snapshot() -> None:
    bus = OperationsBus()
    projector = DecisionProjector(bus)
    try:
        bus.publish(
            DecisionsUpdated(
                cycle=7,
                decisions=(make_decision(), make_decision("MSFT")),
                occurred_at=NOW,
                source="paper-runtime",
            )
        )

        snapshot = projector.snapshot()

        assert snapshot.cycle == 7
        assert snapshot.updated_at == NOW
        assert tuple(item.symbol for item in snapshot.decisions) == (
            "AAPL",
            "MSFT",
        )
        assert snapshot.decisions[0].reasons == (
            "momentum confirmed",
            "risk approved",
        )
    finally:
        projector.close()


def test_new_runtime_clears_stale_decisions() -> None:
    bus = OperationsBus()
    projector = DecisionProjector(bus)
    try:
        bus.publish(
            DecisionsUpdated(
                cycle=1,
                decisions=(make_decision(),),
                occurred_at=NOW,
            )
        )
        bus.publish(RuntimeStarting(occurred_at=NOW))

        assert projector.snapshot() == projector.snapshot().initial()
    finally:
        projector.close()


def test_close_unsubscribes_projector_and_is_idempotent() -> None:
    bus = OperationsBus()
    projector = DecisionProjector(bus)

    assert bus.subscription_count == 2
    projector.close()
    projector.close()

    assert bus.subscription_count == 0
