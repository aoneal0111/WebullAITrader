from datetime import UTC, datetime

from app.gui.projections.atlas_activity_projection import project_atlas_activity
from app.gui.projections.mission_control_projection import (
    project_ai_thinking,
    project_mission_status,
)
from app.operations_core import ApplicationState, RuntimePhase, RuntimeState
from app.read_models.decisions import (
    DecisionExecutionOutcome,
    DecisionRecord,
    DecisionsReadModelSnapshot,
)
from app.read_models.health import HealthState
from app.read_models.watchlist import WatchlistEntry, WatchlistState


def test_atlas_activity_projects_only_existing_runtime_facts() -> None:
    state = ApplicationState(
        runtime=RuntimeState(
            phase=RuntimePhase.RUNNING,
            broker_status="Connected",
            market_feed_status="Healthy",
            inference_status="Healthy",
        ),
        health_projection=HealthState(
            runtime_status="RUNNING",
            broker_status="CONNECTED",
            market_data_status="CONNECTED",
            market_session_status="PREMARKET",
            scanner_status="RUNNING",
            supported_symbols=7300,
            ai_status="RUNNING",
            risk_status="RUNNING",
        ),
        watchlist_projection=WatchlistState(
            ordered_symbols=("XYZ",),
            entries=(WatchlistEntry(
                symbol="XYZ",
                metadata=(("scanner_rank", "1"),),
            ),),
        ),
        decision_projection=DecisionsReadModelSnapshot(decisions=(
            DecisionRecord(
                decision_id="decision-1",
                timestamp=datetime(2026, 8, 6, tzinfo=UTC),
                strategy_id="atlas",
                symbol="XYZ",
                action="BUY",
                confidence=90,
                reasoning_summary="Threshold passed.",
                risk_assessment=None,
                requested_quantity=None,
                resulting_order_id=None,
                execution_outcome=DecisionExecutionOutcome.PENDING,
            ),
        )),
    )

    values = {
        row.label: row.value for row in project_atlas_activity(state).rows
    }

    assert values["Universe Size"] == "7300"
    assert values["AI Candidates"] == "1"
    assert values["Market Data"] == "Connected"
    assert values["Broker"] == "Connected"
    assert values["Symbols Evaluated"] == "Unknown"

    mission = {
        row.label: row.value for row in project_mission_status(state).rows
    }
    assert mission["Objective"] == "Unknown"
    assert mission["Runtime Mode"] == "Paper"
    assert mission["Market Session"] == "Premarket"
    assert mission["AI Scanner"] == "Running"
    assert mission["Decision Engine"] == "Running"
    assert mission["Risk Engine"] == "Running"
    assert mission["Runtime Health"] == "Running"

    thinking = project_ai_thinking(state)
    assert thinking.state == "Decision recorded"
    assert thinking.reasoning == "Threshold passed."
    assert thinking.last_decision == "BUY XYZ"


def test_atlas_activity_does_not_invent_unavailable_statistics() -> None:
    values = {
        row.label: row.value
        for row in project_atlas_activity(ApplicationState()).rows
    }

    assert values["Universe Size"] == "Unknown"
    assert values["Symbols Evaluated"] == "Unknown"
    assert values["AI Candidates"] == "Unknown"


def test_ai_thinking_uses_descriptive_state_without_inventing_reasoning() -> None:
    thinking = project_ai_thinking(ApplicationState(runtime=RuntimeState(
        phase=RuntimePhase.RUNNING,
    )))

    assert thinking.state == "Searching"
    assert thinking.reasoning == "Unknown"
    assert "reasoning" not in thinking.detail.lower()
