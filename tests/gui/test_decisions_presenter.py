from datetime import UTC, datetime

from app.gui.models import DecisionsSnapshot
from app.gui.presenters import DecisionsPresenter
from app.operations_core import ApplicationState
from app.read_models.decisions import (
    DecisionExecutionOutcome,
    DecisionRecord,
    DecisionsReadModelSnapshot,
)


class View:
    def __init__(self) -> None:
        self.snapshot = None

    def render(self, snapshot: DecisionsSnapshot) -> None:
        self.snapshot = snapshot


def test_presenter_formats_immutable_decision_projection() -> None:
    view = View()
    presenter = DecisionsPresenter(view)
    state = ApplicationState(
        decision_projection=DecisionsReadModelSnapshot(
            decisions=(
                DecisionRecord(
                    decision_id="order-1",
                    timestamp=datetime(2026, 7, 30, tzinfo=UTC),
                    strategy_id="momentum-v1",
                    symbol="AAPL",
                    action="BUY",
                    confidence=87,
                    reasoning_summary="Structured rationale",
                    risk_assessment=None,
                    requested_quantity="10",
                    resulting_order_id="order-1",
                    execution_outcome=DecisionExecutionOutcome.ACCEPTED,
                ),
            )
        )
    )

    presenter.render(state)

    assert view.snapshot.rows[0].confidence == "87%"
    assert view.snapshot.rows[0].risk == "--"
    assert view.snapshot.rows[0].outcome == "ACCEPTED"
    assert view.snapshot.selected.title == "BUY AAPL"
    assert view.snapshot.selected.reasoning == "Structured rationale"
    assert view.snapshot.selected.lifecycle == (
        "Decision generated",
        "Order order-1",
        "Accepted",
    )


def test_presenter_selects_decision_for_inspection() -> None:
    view = View()
    presenter = DecisionsPresenter(view)
    decisions = tuple(
        DecisionRecord(
            decision_id=f"decision-{index}",
            timestamp=datetime(2026, 7, 30, index, tzinfo=UTC),
            strategy_id="momentum-v1",
            symbol=symbol,
            action=action,
            confidence=confidence,
            reasoning_summary=reasoning,
            risk_assessment="APPROVED",
            requested_quantity="5",
            resulting_order_id=f"order-{index}",
            execution_outcome=outcome,
        )
        for index, symbol, action, confidence, reasoning, outcome in (
            (
                1,
                "AAPL",
                "BUY",
                80,
                "First decision",
                DecisionExecutionOutcome.ACCEPTED,
            ),
            (
                2,
                "MSFT",
                "SELL",
                65,
                "Second decision",
                DecisionExecutionOutcome.REJECTED,
            ),
        )
    )
    state = ApplicationState(
        decision_projection=DecisionsReadModelSnapshot(decisions=decisions)
    )
    presenter.render(state)

    presenter.select_decision("decision-2")

    assert view.snapshot.selected.decision_id == "decision-2"
    assert view.snapshot.selected.title == "SELL MSFT"
    assert view.snapshot.selected.confidence == "65%"
    assert view.snapshot.selected.execution_outcome == "REJECTED"
