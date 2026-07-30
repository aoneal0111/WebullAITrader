from __future__ import annotations

from app.operations_core import OperationsHealthState
from app.read_models.health.models import HealthState


def project_operational_health(state: OperationsHealthState) -> HealthState:
    if not isinstance(state, OperationsHealthState):
        raise TypeError("state must be an OperationsHealthState")
    return HealthState(
        runtime_status=state.runtime_status,
        broker_status=state.broker_status,
        market_data_status=state.market_data_status,
        ai_status=state.ai_status,
        risk_status=state.risk_status,
        persistence_status=state.persistence_status,
        last_error=state.last_error,
        last_warning=state.last_warning,
        last_heartbeat=state.last_heartbeat,
        connection_latency=state.connection_latency,
        reconnect_attempts=state.reconnect_attempts,
        degraded=state.degraded,
        healthy=state.healthy,
    )
