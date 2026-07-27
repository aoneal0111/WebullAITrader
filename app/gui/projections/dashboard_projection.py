from __future__ import annotations

from app.gui.formatters import format_orders
from app.gui.models import (
    ActivityEntry,
    ActivitySnapshot,
    DashboardSnapshot,
    PositionsSnapshot,
    RuntimeSnapshot,
    RuntimeState,
)
from app.operations_core import ApplicationState
from app.read_models.orders import project_orders_read_model


def project_dashboard(state: ApplicationState) -> DashboardSnapshot:
    runtime = state.runtime
    orders_read_model = project_orders_read_model(state)

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
        activity=ActivitySnapshot(
            entries=tuple(
                ActivityEntry(
                    occurred_at=entry.occurred_at,
                    message=entry.message,
                )
                for entry in state.timeline[-10:][::-1]
            )
        ),
        positions=PositionsSnapshot.initial(),
        orders=format_orders(orders_read_model),
    )
