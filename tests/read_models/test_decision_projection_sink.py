from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.operations.runtime import PaperRuntimeEvent, RuntimeDecision
from app.operations_core import (
    ApplicationStateStore,
    DecisionsUpdated,
    OperationsBus,
    OperationsOrder,
)
from app.paper_trading.models import PaperFill
from app.read_models.decision_projection import DecisionProjection
from app.read_models.decisions import DecisionExecutionOutcome


NOW = datetime(2026, 7, 30, 15, 0, tzinfo=UTC)


def decision(
    decision_id: str = "order-1",
    *,
    symbol: str = "AAPL",
    order_id: str | None = "order-1",
) -> RuntimeDecision:
    return RuntimeDecision(
        decision_id=decision_id,
        timestamp=NOW,
        strategy_id="momentum-v1",
        symbol=symbol,
        action="BUY",
        confidence=87,
        reasoning_summary="Momentum and liquidity gates passed",
        risk_assessment="APPROVED",
        requested_quantity=Decimal("10"),
        resulting_order_id=order_id,
    )


def event(
    sequence: int,
    *,
    decision_fact: RuntimeDecision | None = None,
    status: str | None = None,
    fill: bool = False,
    order_id: str = "order-1",
    symbol: str = "AAPL",
) -> PaperRuntimeEvent:
    timestamp = NOW + timedelta(seconds=sequence)
    order = (
        OperationsOrder(
            order_id=order_id,
            symbol=symbol,
            side="BUY",
            quantity="10",
            status=status,
            updated_at=timestamp,
        )
        if status is not None
        else None
    )
    execution = (
        PaperFill(
            request_id=order_id,
            symbol=symbol,
            side="BUY",
            quantity=Decimal("10"),
            fill_price=Decimal("100"),
            notional=Decimal("1000"),
            realized_pnl=Decimal("0"),
            timestamp=timestamp,
        )
        if fill
        else None
    )
    return PaperRuntimeEvent(
        sequence=sequence,
        timestamp=timestamp,
        event_type="DECISION_PROCESSED",
        message="Structured decision event.",
        cycle=1,
        symbol=symbol,
        order=order,
        fill=execution,
        decision=decision_fact,
    )


def test_successful_decision_updates_one_immutable_logical_record() -> None:
    projection = DecisionProjection(OperationsBus())

    projection(event(1, decision_fact=decision(), status="SUBMITTED"))
    projection(event(2, status="ACCEPTED"))
    projection(event(3, fill=True))

    record = projection.snapshot.decisions[0]
    assert len(projection.snapshot.decisions) == 1
    assert record.execution_outcome is DecisionExecutionOutcome.FILLED
    assert record.strategy_id == "momentum-v1"
    assert record.requested_quantity == "10"
    with pytest.raises(FrozenInstanceError):
        record.action = "SELL"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("REJECTED", DecisionExecutionOutcome.REJECTED),
        ("CANCELLED", DecisionExecutionOutcome.CANCELLED),
        ("PARTIALLY_FILLED", DecisionExecutionOutcome.PARTIALLY_FILLED),
    ],
)
def test_terminal_and_partial_order_outcomes_are_projected(
    status: str,
    expected: DecisionExecutionOutcome,
) -> None:
    projection = DecisionProjection(OperationsBus())
    projection(event(1, decision_fact=decision()))
    projection(event(2, status=status))

    assert projection.snapshot.decisions[0].execution_outcome is expected


def test_multiple_simultaneous_decisions_are_correlated_by_order_id() -> None:
    projection = DecisionProjection(OperationsBus())
    projection(event(1, decision_fact=decision()))
    projection(
        event(
            2,
            decision_fact=decision(
                "order-2",
                symbol="MSFT",
                order_id="order-2",
            ),
            order_id="order-2",
            symbol="MSFT",
        )
    )
    projection(event(3, status="REJECTED"))
    projection(
        event(
            4,
            status="ACCEPTED",
            order_id="order-2",
            symbol="MSFT",
        )
    )

    by_symbol = {
        item.symbol: item.execution_outcome
        for item in projection.snapshot.decisions
    }
    assert by_symbol == {
        "AAPL": DecisionExecutionOutcome.REJECTED,
        "MSFT": DecisionExecutionOutcome.ACCEPTED,
    }


def test_duplicate_events_are_idempotent() -> None:
    bus = OperationsBus()
    published = []
    bus.subscribe(DecisionsUpdated, published.append)
    projection = DecisionProjection(bus)
    submitted = event(1, decision_fact=decision(), status="SUBMITTED")
    accepted = event(2, status="ACCEPTED")

    projection(submitted)
    projection(submitted)
    projection(accepted)
    projection(accepted)

    assert len(published) == 2
    assert len(projection.snapshot.decisions) == 1


def test_unstructured_event_text_never_creates_a_decision() -> None:
    projection = DecisionProjection(OperationsBus())
    projection(
        PaperRuntimeEvent(
            sequence=1,
            timestamp=NOW,
            event_type="DECISION_PROCESSED",
            message="BUY AAPL with 99 percent confidence",
            cycle=1,
            symbol="AAPL",
        )
    )

    assert projection.snapshot.decisions == ()


def test_out_of_order_execution_is_applied_when_decision_arrives() -> None:
    projection = DecisionProjection(OperationsBus())
    projection(event(3, fill=True))
    projection(event(1, decision_fact=decision(), status="SUBMITTED"))
    projection(event(2, status="ACCEPTED"))

    assert (
        projection.snapshot.decisions[0].execution_outcome
        is DecisionExecutionOutcome.FILLED
    )


def test_application_state_exposes_decision_projection() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    projection = DecisionProjection(bus)

    projection(event(1, decision_fact=decision()))

    assert store.snapshot().decision_projection == projection.snapshot
