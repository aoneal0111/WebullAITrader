from __future__ import annotations

from app.gui.models import DashboardSnapshot
from app.operations_core import ApplicationState


def project_dashboard(state: ApplicationState) -> DashboardSnapshot:
    runtime = state.runtime

    return DashboardSnapshot(
        environment=runtime.environment,
        runtime_state=runtime.phase,
        broker_status=runtime.broker_status,
        market_feed_status=runtime.market_feed_status,
        inference_status=runtime.inference_status,
        emergency_stop_enabled=True,
        active_model=runtime.active_model,
        cycle_count=runtime.cycles_completed,
        status_message=runtime.last_error or "Healthy",
    )
