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
            ("Historical Bars", state.historical_bars_status or "--"),
            ("Quotes", state.quotes_status or "--"),
            ("Streaming", state.streaming_status or state.market_data_status or "--"),
            ("Subscription", state.subscription_status or "--"),
            ("Stream Heartbeat", state.heartbeat_status or "--"),
            ("Reconnect", state.reconnect_status or "--"),
            ("Entitlement", state.entitlement_status or "--"),
            ("Current Session", state.market_session_status or "--"),
            ("Scanner Retry", state.scanner_retry_status or "--"),
            ("Market Data Probe 1", state.probe_aapl_status or "--"),
            ("Market Data Probe 2", state.probe_spy_status or "--"),
            ("Market Data Probe 3", state.probe_tsla_status or "--"),
            ("Market Data Probe 4", state.probe_msft_status or "--"),
            ("Market Data Probe 5", state.probe_nvda_status or "--"),
            ("Supported Symbols", str(state.supported_symbols) if state.supported_symbols is not None else "--"),
            ("AI Scanner", state.scanner_status or "--"),
            ("Universe", state.universe_status or "--"),
            ("Symbols", state.symbols_status or "--"),
            ("Reference Cache", state.reference_cache_status or "--"),
            ("Ranking", state.ranking_status or "--"),
            ("AI", state.ai_status or "--"),
            ("Risk", state.risk_status or "--"),
            ("Persistence", state.persistence_status or "--"),
            ("Heartbeat", heartbeat),
            ("Latency", latency),
            ("Reconnects", str(state.reconnect_attempts)),
        ),
        incident=incident,
        capabilities=tuple(
            (entry.name.value, entry.availability.value)
            for entry in state.capabilities.assets
        ),
        sessions=tuple(
            (entry.name.value, entry.availability.value)
            for entry in state.capabilities.sessions
        ),
    )


def _overall(state: HealthState) -> tuple[str, str]:
    if state.healthy:
        return "HEALTHY", "good"
    if state.degraded:
        return "DEGRADED", "warn"
    if state.runtime_status == "STOPPED":
        return "STOPPED", "warn"
    return "UNKNOWN", "warn"
