from __future__ import annotations

from dataclasses import replace

from app.gui.models import (
    DashboardSnapshot,
    RuntimeState,
    project_dashboard_snapshot,
)
from app.operations_core import (
    ApplicationState,
    RuntimePhase,
    RuntimeState as OperationsRuntimeState,
)


def test_dashboard_snapshot_initial_values_are_safe() -> None:
    snapshot = DashboardSnapshot.initial()

    assert snapshot.environment == "PAPER"
    assert snapshot.runtime_state is RuntimeState.STOPPED
    assert snapshot.broker_status == "Disconnected"
    assert snapshot.market_feed_status == "Idle"
    assert snapshot.emergency_stop_enabled is True
    assert snapshot.cycle_count == 0


def test_projection_maps_running_runtime() -> None:
    runtime = OperationsRuntimeState(
        phase=RuntimePhase.RUNNING,
        environment="PAPER",
        broker_status="Connected",
        market_feed_status="Healthy",
        inference_status="Healthy",
        active_model="momentum-v1",
        cycles_completed=42,
    )

    snapshot = project_dashboard_snapshot(
        ApplicationState(runtime=runtime)
    )

    assert snapshot.runtime_state is RuntimeState.RUNNING
    assert snapshot.environment == "PAPER"
    assert snapshot.broker_status == "Connected"
    assert snapshot.market_feed_status == "Healthy"
    assert snapshot.inference_status == "Healthy"
    assert snapshot.active_model == "momentum-v1"
    assert snapshot.cycle_count == 42


def test_projection_maps_failed_runtime_to_error() -> None:
    runtime = OperationsRuntimeState(
        phase=RuntimePhase.FAILED,
        market_feed_status="Error",
        inference_status="Error",
        last_error="Market feed unavailable",
    )

    snapshot = project_dashboard_snapshot(
        ApplicationState(runtime=runtime)
    )

    assert snapshot.runtime_state is RuntimeState.ERROR
    assert snapshot.status_message == "Market feed unavailable"


def test_projection_does_not_mutate_application_state() -> None:
    state = ApplicationState()
    original = replace(state)

    project_dashboard_snapshot(state)

    assert state == original
