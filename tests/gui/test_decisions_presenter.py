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
