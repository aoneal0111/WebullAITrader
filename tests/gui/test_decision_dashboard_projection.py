from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.gui.models import DecisionCenterSnapshot
from app.gui.projections.dashboard_projection import project_dashboard
from app.operations_core import ApplicationState
from app.read_models.decisions import DecisionReadModel, DecisionsReadModelSnapshot


NOW = datetime(2026, 7, 28, 14, 5, tzinfo=timezone.utc)


def test_dashboard_formats_decisions_for_read_only_center() -> None:
    decisions = DecisionsReadModelSnapshot(
        cycle=3,
        updated_at=NOW,
        decisions=(
            DecisionReadModel(
                symbol="AAPL",
                action="ENTER_LONG",
                confidence=91,
                score=Decimal("1.25"),
                reasons=("breakout", "volume"),
                source_action="BUY",
                position_quantity=Decimal("0"),
                strategy_version="2.0",
                decided_at=NOW,
            ),
        ),
    )

    dashboard = project_dashboard(ApplicationState(), decisions)

    assert dashboard.decisions.cycle == "Cycle 3"
    assert dashboard.decisions.rows[0].symbol == "AAPL"
    assert dashboard.decisions.rows[0].action == "ENTER LONG"
    assert dashboard.decisions.rows[0].confidence == "91%"
    assert dashboard.decisions.rows[0].score == "1.25"
    assert dashboard.decisions.rows[0].rationale == "breakout | volume"


def test_dashboard_defaults_to_empty_decision_center() -> None:
    assert project_dashboard(ApplicationState()).decisions == (
        DecisionCenterSnapshot.initial()
    )


def test_dashboard_rejects_non_decision_snapshot() -> None:
    with pytest.raises(TypeError, match="DecisionsReadModelSnapshot"):
        project_dashboard(ApplicationState(), object())  # type: ignore[arg-type]
