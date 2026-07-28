from datetime import datetime, timezone
from decimal import Decimal

from app.composition.paper_runtime_projection import (
    create_operations_decisions,
    create_paper_runtime_result_publisher,
)
from app.operations import PaperRuntimeCycleResult
from app.operations_core import (
    DecisionsUpdated,
    OperationsBus,
    PaperRuntimeUpdated,
    PositionsUpdated,
)
from app.paper_session import create_paper_session
from app.read_models.decisions import DecisionProjector
from app.strategy_engine import StrategyDecision, StrategyDecisionAction


NOW = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)


def make_result() -> PaperRuntimeCycleResult:
    decision = StrategyDecision(
        symbol="AAPL",
        action=StrategyDecisionAction.ENTER_LONG,
        confidence=84,
        score=Decimal("0.84"),
        timestamp=NOW,
        reasons=("trend", "volume"),
        source_action="BUY",
        position_quantity=Decimal("0"),
        strategy_version="2.1",
    )
    return PaperRuntimeCycleResult(
        cycle=4,
        timestamp=NOW,
        symbols=("AAPL",),
        decisions=(decision,),
        session=create_paper_session(
            session_id="paper-1",
            initial_cash=Decimal("10000"),
            started_at=NOW,
        ),
    )


def test_operations_decisions_are_broker_neutral_immutable_values() -> None:
    mapped = create_operations_decisions(make_result())

    assert len(mapped) == 1
    assert mapped[0].symbol == "AAPL"
    assert mapped[0].action == "ENTER_LONG"
    assert mapped[0].confidence == 84
    assert mapped[0].reasons == ("trend", "volume")
    assert mapped[0].strategy_version == "2.1"


def test_runtime_publisher_projects_decisions_before_other_cycle_slices() -> None:
    bus = OperationsBus()
    projector = DecisionProjector(bus)
    event_types: list[type] = []
    subscriptions = (
        bus.subscribe(DecisionsUpdated, lambda event: event_types.append(type(event))),
        bus.subscribe(PaperRuntimeUpdated, lambda event: event_types.append(type(event))),
        bus.subscribe(PositionsUpdated, lambda event: event_types.append(type(event))),
    )
    try:
        create_paper_runtime_result_publisher(bus)(make_result())

        assert event_types == [
            DecisionsUpdated,
            PaperRuntimeUpdated,
            PositionsUpdated,
        ]
        snapshot = projector.snapshot()
        assert snapshot.cycle == 4
        assert snapshot.updated_at == NOW
        assert snapshot.decisions[0].action == "ENTER_LONG"
    finally:
        for subscription in subscriptions:
            bus.unsubscribe(subscription)
        projector.close()
