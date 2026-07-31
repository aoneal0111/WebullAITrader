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
            ("Trading Environment", state.trading_environment or "--"),
            ("Trading REST", state.trading_rest_status or state.broker_status or "--"),
            ("Account", state.account_status or "--"),
            ("Buying Power", state.buying_power_status or "--"),
            ("Positions", state.positions_status or "--"),
            ("Orders", state.orders_status or "--"),
            ("Balances", state.balances_status or "--"),
            ("Market Data Environment", state.market_data_environment or "--"),
            ("Market Data REST", state.market_data_rest_status or "--"),
            ("Streaming", state.streaming_status or state.market_data_status or "--"),
            ("Subscription", state.subscription_status or "--"),
            ("Stream Heartbeat", state.heartbeat_status or "--"),
            ("Reconnect", state.reconnect_status or "--"),
            ("Entitlement", state.entitlement_status or "--"),
            ("Probe AAPL", state.probe_aapl_status or "--"),
            ("Probe SPY", state.probe_spy_status or "--"),
            ("Probe TSLA", state.probe_tsla_status or "--"),
            ("Supported Symbols", str(state.supported_symbols) if state.supported_symbols is not None else "--"),
            ("Scanner", state.scanner_status or "--"),
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
