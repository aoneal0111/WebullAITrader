from app.gui.models import DashboardSnapshot, RuntimeState


def test_initial_dashboard_snapshot_is_safe_and_stopped() -> None:
    snapshot = DashboardSnapshot.initial()

    assert snapshot.environment == "PAPER"
    assert snapshot.runtime_state is RuntimeState.STOPPED
    assert snapshot.broker_status == "Disconnected"
    assert snapshot.emergency_stop_enabled is True
    assert snapshot.cycle_count == 0


def test_runtime_states_have_stable_values() -> None:
    assert RuntimeState.STOPPED.value == "STOPPED"
    assert RuntimeState.STARTING.value == "STARTING"
    assert RuntimeState.RUNNING.value == "RUNNING"
    assert RuntimeState.STOPPING.value == "STOPPING"
    assert RuntimeState.ERROR.value == "ERROR"