from __future__ import annotations

from dataclasses import replace
from threading import RLock

from app.operations.runtime import PaperRuntimeEvent
from app.operations_core import (
    HealthUpdated,
    OperationsBus,
    OperationsHealthState,
)
from app.read_models.health import HealthState


_HEALTHY = frozenset({"CONNECTED", "HEALTHY", "READY", "RUNNING"})
_UNHEALTHY = frozenset(
    {
        "DEGRADED",
        "CONNECTING",
        "DISCONNECTED",
        "ERROR",
        "FAILED",
        "STOPPED",
        "UNAVAILABLE",
    }
)


class HealthProjection:
    """Fold infrastructure runtime events into one immutable health state."""

    def __init__(self, bus: OperationsBus) -> None:
        if not isinstance(bus, OperationsBus):
            raise TypeError("bus must be an OperationsBus")
        self._bus = bus
        self._lock = RLock()
        self._snapshot = HealthState.initial()
        self._seen_events: frozenset[tuple[str, int]] = frozenset()

    @property
    def snapshot(self) -> HealthState:
        with self._lock:
            return self._snapshot

    def __call__(self, event: PaperRuntimeEvent) -> None:
        if not isinstance(event, PaperRuntimeEvent):
            raise TypeError("event must be a PaperRuntimeEvent")
        identity = (event.source, event.sequence)
        with self._lock:
            if identity in self._seen_events:
                return
            self._seen_events = self._seen_events | {identity}
            projected = _reduce_health(self._snapshot, event)
            if projected == self._snapshot:
                return
            self._snapshot = projected

        self._bus.publish(
            HealthUpdated(
                occurred_at=event.timestamp,
                source="paper-runtime-health-projection",
                state=_to_operations(projected),
            )
        )


def _reduce_health(
    current: HealthState,
    event: PaperRuntimeEvent,
) -> HealthState:
    event_type = event.event_type.strip().upper()
    changes: dict[str, object] = {}
    _apply_inferred_statuses(changes, event_type)

    if "HEARTBEAT" in event_type:
        changes["last_heartbeat"] = event.timestamp
    if "RECONNECT_ATTEMPT" in event_type:
        changes["reconnect_attempts"] = current.reconnect_attempts + 1
    if _is_warning(event_type):
        changes["last_warning"] = event.message
    if _is_error(event_type):
        changes["last_error"] = event.message
        _apply_error_status(changes, event_type)

    health = event.health
    if health is not None:
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
            "scanner_status",
            "ai_status",
            "risk_status",
            "persistence_status",
            "last_error",
            "last_warning",
        ):
            value = getattr(health, field_name)
            if value is not None:
                changes[field_name] = value.upper() if field_name.endswith(
                    "_status"
                ) else value
        if health.supported_symbols is not None:
            changes["supported_symbols"] = health.supported_symbols
        if health.heartbeat_at is not None:
            changes["last_heartbeat"] = health.heartbeat_at
        if health.connection_latency is not None:
            changes["connection_latency"] = format(
                health.connection_latency,
                "f",
            )
        if health.reconnect_attempts is not None:
            changes["reconnect_attempts"] = health.reconnect_attempts

    candidate = replace(current, **changes)
    healthy, degraded = _derive_flags(candidate)
    return replace(candidate, healthy=healthy, degraded=degraded)


def _apply_inferred_statuses(
    changes: dict[str, object],
    event_type: str,
) -> None:
    exact = {
        "STARTING": ("runtime_status", "STARTING"),
        "RUNTIME_STARTING": ("runtime_status", "STARTING"),
        "STARTED": ("runtime_status", "RUNNING"),
        "RUNTIME_STARTED": ("runtime_status", "RUNNING"),
        "STOPPED": ("runtime_status", "STOPPED"),
        "RUNTIME_STOPPED": ("runtime_status", "STOPPED"),
        "PAUSED": ("runtime_status", "PAUSED"),
        "RUNTIME_PAUSED": ("runtime_status", "PAUSED"),
        "RESUMED": ("runtime_status", "RUNNING"),
        "RUNTIME_RESUMED": ("runtime_status", "RUNNING"),
        "FAILED": ("runtime_status", "FAILED"),
        "RUNTIME_FAILED": ("runtime_status", "FAILED"),
        "BROKER_CONNECTED": ("broker_status", "CONNECTED"),
        "BROKER_RECONNECTED": ("broker_status", "CONNECTED"),
        "BROKER_RECONNECT_ATTEMPT": ("broker_status", "CONNECTING"),
        "BROKER_DISCONNECTED": ("broker_status", "DISCONNECTED"),
        "MARKET_DATA_CONNECTED": ("market_data_status", "CONNECTED"),
        "MARKET_FEED_CONNECTED": ("market_data_status", "CONNECTED"),
        "MARKET_DATA_RECONNECTED": ("market_data_status", "CONNECTED"),
        "MARKET_DATA_RECONNECT_ATTEMPT": (
            "market_data_status",
            "CONNECTING",
        ),
        "MARKET_DATA_DISCONNECTED": (
            "market_data_status",
            "DISCONNECTED",
        ),
        "MARKET_DATA_LOST": ("market_data_status", "DISCONNECTED"),
        "MARKET_FEED_DISCONNECTED": (
            "market_data_status",
            "DISCONNECTED",
        ),
        "AI_READY": ("ai_status", "READY"),
        "MODEL_LOADED": ("ai_status", "READY"),
        "AI_UNAVAILABLE": ("ai_status", "UNAVAILABLE"),
        "AI_FAILED": ("ai_status", "FAILED"),
        "RISK_READY": ("risk_status", "READY"),
        "RISK_DEGRADED": ("risk_status", "DEGRADED"),
        "RISK_FAILED": ("risk_status", "FAILED"),
        "PERSISTENCE_READY": ("persistence_status", "READY"),
        "PERSISTENCE_DEGRADED": ("persistence_status", "DEGRADED"),
        "PERSISTENCE_FAILED": ("persistence_status", "FAILED"),
    }
    update = exact.get(event_type)
    if update is not None:
        changes[update[0]] = update[1]


def _apply_error_status(
    changes: dict[str, object],
    event_type: str,
) -> None:
    prefixes = (
        ("BROKER_", "broker_status"),
        ("MARKET_DATA_", "market_data_status"),
        ("MARKET_FEED_", "market_data_status"),
        ("AI_", "ai_status"),
        ("MODEL_", "ai_status"),
        ("RISK_", "risk_status"),
        ("PERSISTENCE_", "persistence_status"),
    )
    for prefix, field_name in prefixes:
        if event_type.startswith(prefix):
            changes.setdefault(field_name, "ERROR")
            return
    changes["runtime_status"] = "FAILED"


def _derive_flags(state: HealthState) -> tuple[bool, bool]:
    if state.runtime_status != "RUNNING":
        return False, state.runtime_status == "FAILED"
    required = (
        state.broker_status,
        state.market_data_status,
        state.ai_status,
    )
    optional = (state.risk_status, state.persistence_status)
    unhealthy = any(value in _UNHEALTHY for value in (*required, *optional))
    complete = all(value in _HEALTHY for value in required)
    optional_healthy = all(
        value is None or value in _HEALTHY
        for value in optional
    )
    healthy = complete and optional_healthy and not unhealthy
    return healthy, unhealthy


def _is_warning(event_type: str) -> bool:
    return "WARNING" in event_type or event_type.endswith("_WARN")


def _is_error(event_type: str) -> bool:
    return (
        "ERROR" in event_type
        or event_type in {"FAILED", "RUNTIME_FAILED"}
        or event_type.endswith("_FAILED")
        or event_type.endswith("_FAILURE")
    )


def _to_operations(state: HealthState) -> OperationsHealthState:
    return OperationsHealthState(
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
        streaming_status=state.streaming_status,
        subscription_status=state.subscription_status,
        heartbeat_status=state.heartbeat_status,
        reconnect_status=state.reconnect_status,
        entitlement_status=state.entitlement_status,
        probe_aapl_status=state.probe_aapl_status,
        probe_spy_status=state.probe_spy_status,
        probe_tsla_status=state.probe_tsla_status,
        scanner_status=state.scanner_status,
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
    )


__all__ = ["HealthProjection"]
