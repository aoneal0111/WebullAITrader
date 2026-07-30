from __future__ import annotations

from app.gui.formatters import format_orders, format_positions
from app.gui.models import (
    DashboardSnapshot,
    RuntimeSnapshot,
    RuntimeState,
)
from app.operations_core import ApplicationState
from app.read_models.orders import project_orders_read_model
from app.read_models.positions import project_positions_read_model
from app.gui.projections.activity_projection import project_timeline_activity


def project_dashboard(state: ApplicationState) -> DashboardSnapshot:
    runtime = state.runtime
    orders_read_model = project_orders_read_model(state)
    positions_read_model = project_positions_read_model(state)

    return DashboardSnapshot(
        runtime=RuntimeSnapshot(
            environment=runtime.environment,
            state=RuntimeState(runtime.phase.value),
            broker_status=runtime.broker_status,
            market_feed_status=runtime.market_feed_status,
            inference_status=runtime.inference_status,
            emergency_stop_enabled=True,
            active_model=runtime.active_model,
            cycle_count=runtime.cycles_completed,
            status_message=runtime.last_error or "Healthy",
        ),
        activity=project_timeline_activity(state, limit=10),
        positions=format_positions(positions_read_model),
        orders=format_orders(orders_read_model),
    )
