from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class HealthState:
    runtime_status: str | None = None
    broker_status: str | None = None
    market_data_status: str | None = None
    trading_environment: str | None = None
    trading_rest_status: str | None = None
    account_status: str | None = None
    buying_power_status: str | None = None
    positions_status: str | None = None
    orders_status: str | None = None
    balances_status: str | None = None
    market_data_environment: str | None = None
    market_data_rest_status: str | None = None
    streaming_status: str | None = None
    subscription_status: str | None = None
    heartbeat_status: str | None = None
    reconnect_status: str | None = None
    entitlement_status: str | None = None
    probe_aapl_status: str | None = None
    probe_spy_status: str | None = None
    probe_tsla_status: str | None = None
    probe_msft_status: str | None = None
    probe_nvda_status: str | None = None
    scanner_status: str | None = None
    universe_status: str | None = None
    symbols_status: str | None = None
    reference_cache_status: str | None = None
    ranking_status: str | None = None
    supported_symbols: int | None = None
    ai_status: str | None = None
    risk_status: str | None = None
    persistence_status: str | None = None
    last_error: str | None = None
    last_warning: str | None = None
    last_heartbeat: datetime | None = None
    connection_latency: str | None = None
    reconnect_attempts: int = 0
    degraded: bool = False
    healthy: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "runtime_status",
            "broker_status",
            "market_data_status",
            "trading_environment",
            "trading_rest_status",
            "account_status",
            "buying_power_status",
            "positions_status",
            "orders_status",
            "balances_status",
            "market_data_environment",
            "market_data_rest_status",
            "streaming_status",
            "subscription_status",
            "heartbeat_status",
            "reconnect_status",
            "entitlement_status",
            "probe_aapl_status",
            "probe_spy_status",
            "probe_tsla_status",
            "probe_msft_status",
            "probe_nvda_status",
            "scanner_status",
            "universe_status",
            "symbols_status",
            "reference_cache_status",
            "ranking_status",
            "ai_status",
            "risk_status",
            "persistence_status",
            "last_error",
            "last_warning",
            "connection_latency",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
            ):
                raise ValueError(
                    f"health {field_name} must be None or stripped text"
                )
        if self.supported_symbols is not None and (
            isinstance(self.supported_symbols, bool)
            or not isinstance(self.supported_symbols, int)
            or self.supported_symbols < 0
        ):
            raise ValueError("health supported symbols must be nonnegative")
        if (
            self.last_heartbeat is not None
            and self.last_heartbeat.tzinfo is None
        ):
            raise ValueError("last_heartbeat must be timezone-aware")
        if (
            isinstance(self.reconnect_attempts, bool)
            or not isinstance(self.reconnect_attempts, int)
            or self.reconnect_attempts < 0
        ):
            raise ValueError("reconnect_attempts must be nonnegative")
        if not isinstance(self.degraded, bool):
            raise TypeError("degraded must be a bool")
        if not isinstance(self.healthy, bool):
            raise TypeError("healthy must be a bool")
        if self.healthy and self.degraded:
            raise ValueError("health cannot be healthy and degraded")

    @classmethod
    def initial(cls) -> "HealthState":
        return cls()
