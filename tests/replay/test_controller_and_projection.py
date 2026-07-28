from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.composition import create_desktop_composition
from app.gui.projections.dashboard_projection import project_dashboard
from app.operations_core import (
    DecisionsUpdated,
    OperationsDecision,
    OperatorTradeSelected,
    RuntimeStarted,
    RuntimeStarting,
    TradeLifecycleUpdated,
)
from app.replay import (
    ReplayEventArchive,
    ReplayState,
    ReplayStatus,
)


NOW = datetime(2026, 7, 28, 19, 0, tzinfo=timezone.utc)


def _archive() -> ReplayEventArchive:
    decision = OperationsDecision(
        symbol="AAPL",
        action="ENTER_LONG",
        confidence=90,
        score=Decimal("0.9"),
        reasons=("approved",),
        source_action="BUY",
        position_quantity=Decimal("0"),
        strategy_version="1.0",
        decided_at=NOW + timedelta(seconds=2),
    )
    return ReplayEventArchive.from_events(
        (
            RuntimeStarting(occurred_at=NOW),
            RuntimeStarted(
                active_model="atlas",
                occurred_at=NOW + timedelta(seconds=1),
            ),
            DecisionsUpdated(
                cycle=1,
                decisions=(decision,),
                occurred_at=NOW + timedelta(seconds=2),
            ),
            TradeLifecycleUpdated(
                symbol="AAPL",
                phase="SCANNED",
                title="Scanned",
                description="Candidate selected.",
                occurred_at=NOW + timedelta(seconds=3),
            ),
            OperatorTradeSelected(
                symbol="AAPL",
                occurred_at=NOW + timedelta(seconds=4),
            ),
        )
    )


def test_controller_reconstructs_all_existing_projection_types() -> None:
    composition = create_desktop_composition()
    archive = _archive()
    try:
        composition.replay_controller.load(
            archive,
            session_id="session-2026-07-28",
        )
        composition.replay_controller.seek(len(archive.entries))

        graph = composition.replay_projections
        replay = composition.replay_controller.snapshot()
        dashboard = project_dashboard(
            graph.state_store.snapshot(),
            graph.decision_projector.snapshot(),
            graph.runtime_health_projector.snapshot(),
            graph.timeline_projector.snapshot(),
            graph.trade_lifecycle_projector.snapshot(),
            graph.operator_workspace_projector.snapshot(),
            replay,
        )

        assert composition.state_store.snapshot().revision == 0
        assert replay.state is ReplayState.REPLAY
        assert replay.status is ReplayStatus.COMPLETED
        assert dashboard.replay is replay
        assert dashboard.runtime.state.value == "RUNNING"
        assert dashboard.decisions.rows[0].symbol == "AAPL"
        assert dashboard.lifecycle_explorer.selected_symbol == "AAPL"
        assert dashboard.operator_workspace.selected_trade == "AAPL"
        assert len(dashboard.timeline.rows) == len(archive.entries)

        composition.replay_controller.step_backward()
        assert (
            composition.replay_projections
            .operator_workspace_projector
            .snapshot()
            .selected_trade
            is None
        )
        assert (
            composition.replay_projections
            .decision_projector
            .snapshot()
            .decisions[0]
            .symbol
            == "AAPL"
        )
    finally:
        composition.close(timeout_seconds=1.0)


def test_controller_notifies_with_immutable_snapshots_and_closes() -> None:
    composition = create_desktop_composition()
    snapshots = []
    listener_id = composition.replay_controller.subscribe(
        snapshots.append
    )
    try:
        composition.replay_controller.load(_archive())
        composition.replay_controller.step_forward()
        composition.replay_controller.pause()

        assert snapshots[0].state is ReplayState.LIVE
        assert snapshots[-1].status is ReplayStatus.PAUSED
        assert composition.replay_controller.unsubscribe(listener_id)
        assert not composition.replay_controller.unsubscribe(listener_id)
    finally:
        composition.close(timeout_seconds=1.0)
        composition.close(timeout_seconds=1.0)
