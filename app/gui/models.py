from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.operations_core import ApplicationState, RuntimePhase


class RuntimeState(StrEnum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    environment: str
    runtime_state: RuntimeState
    broker_status: str
    market_feed_status: str
    inference_status: str
    emergency_stop_enabled: bool
    active_model: str
    cycle_count: int
    status_message: str

    @classmethod
    def initial(cls) -> "DashboardSnapshot":
        return cls(
            environment="PAPER",
            runtime_state=RuntimeState.STOPPED,
            broker_status="Disconnected",
            market_feed_status="Idle",
            inference_status="Ready",
            emergency_stop_enabled=True,
            active_model="Not loaded",
            cycle_count=0,
            status_message="Ready to start.",
        )


_PHASE_MAP: dict[RuntimePhase, RuntimeState] = {
    RuntimePhase.STOPPED: RuntimeState.STOPPED,
    RuntimePhase.STARTING: RuntimeState.STARTING,
    RuntimePhase.RUNNING: RuntimeState.RUNNING,
    RuntimePhase.STOPPING: RuntimeState.STOPPING,
    RuntimePhase.FAILED: RuntimeState.ERROR,
}


def project_dashboard_snapshot(state: ApplicationState) -> DashboardSnapshot:
    """Project application state into the dashboard's immutable view model."""

    runtime = state.runtime
    runtime_state = _PHASE_MAP[runtime.phase]

    if runtime.last_error:
        status_message = runtime.last_error
    elif state.timeline:
        status_message = state.timeline[-1].message
    elif runtime.phase is RuntimePhase.RUNNING:
        status_message = "Paper runtime is operating normally."
    else:
        status_message = "Ready to start."

    return DashboardSnapshot(
        environment=runtime.environment,
        runtime_state=runtime_state,
        broker_status=runtime.broker_status,
        market_feed_status=runtime.market_feed_status,
        inference_status=runtime.inference_status,
        emergency_stop_enabled=True,
        active_model=runtime.active_model,
        cycle_count=runtime.cycles_completed,
        status_message=status_message,
    )
