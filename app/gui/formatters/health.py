from __future__ import annotations

from app.gui.models.health import HealthDashboardSnapshot
from app.read_models.health import HealthState


def format_health(state: HealthState) -> HealthDashboardSnapshot:
    if not isinstance(state, HealthState):
        raise TypeError("state must be a HealthState")
    overall, level = _overall(state)
    heartbeat = (
        state.last_heartbeat.astimezone().strftime("%H:%M:%S")
        if state.last_heartbeat is not None
        else "--"
    )
    latency = (
        f"{state.connection_latency} ms"
        if state.connection_latency is not None
        else "--"
    )
    incident = state.last_error or state.last_warning or "No incidents."
    return HealthDashboardSnapshot(
        overall_status=overall,
        status_level=level,
        metrics=(
            ("Runtime", state.runtime_status or "--"),
            ("Broker", state.broker_status or "--"),
            ("Market Data", state.market_data_status or "--"),
            ("AI", state.ai_status or "--"),
            ("Risk", state.risk_status or "--"),
            ("Persistence", state.persistence_status or "--"),
            ("Heartbeat", heartbeat),
            ("Latency", latency),
            ("Reconnects", str(state.reconnect_attempts)),
        ),
        incident=incident,
    )


def _overall(state: HealthState) -> tuple[str, str]:
    if state.healthy:
        return "HEALTHY", "good"
    if state.degraded:
        return "DEGRADED", "warn"
    if state.runtime_status == "STOPPED":
        return "STOPPED", "warn"
    return "UNKNOWN", "warn"
