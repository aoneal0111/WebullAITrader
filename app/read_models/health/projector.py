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
        trading_environment=state.trading_environment,
        trading_rest_status=state.trading_rest_status,
        account_status=state.account_status,
        buying_power_status=state.buying_power_status,
        positions_status=state.positions_status,
        orders_status=state.orders_status,
        balances_status=state.balances_status,
        market_data_environment=state.market_data_environment,
        market_data_rest_status=state.market_data_rest_status,
        historical_bars_status=state.historical_bars_status,
        quotes_status=state.quotes_status,
        streaming_status=state.streaming_status,
        subscription_status=state.subscription_status,
        heartbeat_status=state.heartbeat_status,
        reconnect_status=state.reconnect_status,
        entitlement_status=state.entitlement_status,
        market_session_status=state.market_session_status,
        scanner_retry_status=state.scanner_retry_status,
        probe_aapl_status=state.probe_aapl_status,
        probe_spy_status=state.probe_spy_status,
        probe_tsla_status=state.probe_tsla_status,
        probe_msft_status=state.probe_msft_status,
        probe_nvda_status=state.probe_nvda_status,
        scanner_status=state.scanner_status,
        universe_status=state.universe_status,
        symbols_status=state.symbols_status,
        reference_cache_status=state.reference_cache_status,
        ranking_status=state.ranking_status,
        supported_symbols=state.supported_symbols,
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
        capabilities=state.capabilities,
    )
