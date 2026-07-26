from __future__ import annotations

from app.gui.models import (
    ActivityEntry,
    ActivitySnapshot,
    DashboardSnapshot,
    OrdersSnapshot,
    PositionsSnapshot,
    RuntimeSnapshot,
    RuntimeState,
)
from app.operations_core import ApplicationState


def project_dashboard(state: ApplicationState) -> DashboardSnapshot:
    runtime = state.runtime

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
        orders=OrdersSnapshot.initial(),
    )
