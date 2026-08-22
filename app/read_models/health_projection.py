from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
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

    def __init__(
        self,
        bus: OperationsBus,
        *,
        market_data_stale_after: timedelta = timedelta(seconds=30),
    ) -> None:
        if not isinstance(bus, OperationsBus):
            raise TypeError("bus must be an OperationsBus")
        if market_data_stale_after.total_seconds() <= 0:
            raise ValueError("market_data_stale_after must be positive")
        self._bus = bus
        self._lock = RLock()
        self._snapshot = replace(
            HealthState.initial(),
            market_data_stale_after_seconds=market_data_stale_after.total_seconds(),
        )
        self._seen_events: frozenset[tuple[str, int]] = frozenset()
        self._latest_sequence: dict[str, int] = {}
        self._field_timestamps: dict[str, datetime] = {}

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
            # Runtime sources own monotonically increasing sequences.  A
            # delayed event from the same source must never roll a projection
            # back after a newer observation has already been applied.
            if event.sequence <= self._latest_sequence.get(event.source, 0):
                self._seen_events = self._seen_events | {identity}
                return
            self._seen_events = self._seen_events | {identity}
            self._latest_sequence[event.source] = event.sequence
            changes = _health_changes(self._snapshot, event)
            accepted: dict[str, object] = {}
            for field_name, value in changes.items():
                last_timestamp = self._field_timestamps.get(field_name)
                if last_timestamp is not None and event.timestamp < last_timestamp:
                    continue
                accepted[field_name] = value
                self._field_timestamps[field_name] = event.timestamp
            candidate = replace(self._snapshot, **accepted)
            healthy, degraded = _derive_flags(candidate)
            projected = replace(candidate, healthy=healthy, degraded=degraded)
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
    changes = _health_changes(current, event)
    candidate = replace(current, **changes)
    healthy, degraded = _derive_flags(candidate)
    return replace(candidate, healthy=healthy, degraded=degraded)


def _health_changes(
    current: HealthState,
    event: PaperRuntimeEvent,
) -> dict[str, object]:
    event_type = event.event_type.strip().upper()
    changes: dict[str, object] = {}
    _apply_inferred_statuses(changes, event_type)

    if _is_authoritative_stream_event(event):
        changes["last_market_data_event"] = event.timestamp

    if "HEARTBEAT" in event_type:
        changes["last_heartbeat"] = event.timestamp
    if "RECONNECT_ATTEMPT" in event_type:
        changes["reconnect_attempts"] = current.reconnect_attempts + 1
    if event_type == "HISTORICAL_BARS_LOADED" and _market_data_warning(
        current.last_warning, rest_only=True
    ):
        changes["last_warning"] = None
    if _is_authoritative_stream_event(event) and (
        _market_data_warning(current.last_warning)
    ):
        changes["last_warning"] = None
    if _is_warning(event_type):
        changes["last_warning"] = event.message
    if _is_error(event_type):
        changes["last_error"] = event.message
        _apply_error_status(changes, event_type)

    if event_type in {"BROKER_AUTHENTICATED", "BROKER_REST_OBSERVED"}:
        stream_failed = (
            current.market_data_status in _UNHEALTHY
            or current.streaming_status in _UNHEALTHY
            or (current.streaming_status or "").endswith("FAILED")
        )
        if current.broker_status in _UNHEALTHY and not stream_failed:
            changes["last_error"] = None

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
            "historical_bars_status",
            "quotes_status",
            "streaming_status",
            "subscription_status",
            "heartbeat_status",
            "reconnect_status",
            "entitlement_status",
            "market_session_status",
            "scanner_retry_status",
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
        ):
            value = getattr(health, field_name)
            if value is not None:
                changes[field_name] = value.upper() if field_name.endswith(
                    "_status"
                ) else value
        if health.supported_symbols is not None:
            changes["supported_symbols"] = health.supported_symbols
        if health.subscription_symbols is not None:
            changes["subscription_symbols"] = health.subscription_symbols
        if health.heartbeat_at is not None:
            changes["last_heartbeat"] = health.heartbeat_at
        if health.connection_latency is not None:
            changes["connection_latency"] = format(
                health.connection_latency,
                "f",
            )
        if health.reconnect_attempts is not None:
            changes["reconnect_attempts"] = health.reconnect_attempts
        if health.capabilities is not None:
            changes["capabilities"] = health.capabilities
            if (
                (current.last_warning or "").startswith(
                    "Overnight market-data subscription unavailable."
                )
                and any(
                    entry.availability.value == "Available"
                    for entry in health.capabilities.sessions
                )
            ):
                changes["last_warning"] = None
        if health.streaming_status is not None and health.streaming_status.upper() in {
            "CONNECTED",
            "STREAM_CONNECTED",
        }:
            if current.market_data_status in _UNHEALTHY or (
                current.streaming_status in _UNHEALTHY
                or (current.streaming_status or "").endswith("FAILED")
            ):
                changes["last_error"] = None
            if _market_data_warning(current.last_warning):
                changes["last_warning"] = None

    return changes


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
        "BROKER_AUTHENTICATED": ("broker_status", "CONNECTED"),
        "BROKER_REST_OBSERVED": ("broker_status", "CONNECTED"),
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
    if event_type == "HISTORICAL_BARS_LOADED":
        changes.update(
            runtime_status="RUNNING",
            market_data_status="CONNECTED",
            market_data_rest_status="AVAILABLE",
            historical_bars_status="AVAILABLE",
        )
    elif event_type in {
        "MARKET_DATA_PAYLOAD_RECEIVED",
        "MARKET_DATA_QUOTE_RECEIVED",
        "QUOTE_RECEIVED",
        "MARKET_DATA_TRADE_RECEIVED",
        "TRADE_RECEIVED",
        "MARKET_DATA_SNAPSHOT_RECEIVED",
        "SNAPSHOT_RECEIVED",
    }:
        changes.update(
            runtime_status="RUNNING",
            market_data_status="CONNECTED",
            streaming_status="CONNECTED",
            subscription_status="ACCEPTED",
            last_error=None,
        )
        if "QUOTE" in event_type:
            changes["quotes_status"] = "AVAILABLE"
    elif event_type == "MARKET_DATA_RECONNECTED":
        changes.update(
            runtime_status="RUNNING",
            streaming_status="CONNECTED",
            last_error=None,
        )
    elif event_type in {"MARKET_DATA_SUBSCRIBED", "CHANNELS_SUBSCRIBED"}:
        changes.update(
            runtime_status="RUNNING",
            market_data_status="CONNECTED",
            streaming_status="CONNECTED",
            subscription_status="ACCEPTED",
        )


def _apply_error_status(
    changes: dict[str, object],
    event_type: str,
) -> None:
    if event_type == "STARTUP_VALIDATION_FAILED":
        # Scanner capability validation is not a runtime/infrastructure failure.
        changes.setdefault("scanner_status", "STOPPED")
        return
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
    market_data_authoritative = (
        state.market_data_rest_status in {"CONNECTED", "AVAILABLE"}
        and state.historical_bars_status == "AVAILABLE"
        and state.streaming_status == "CONNECTED"
        and state.quotes_status == "AVAILABLE"
    )
    market_status = "CONNECTED" if market_data_authoritative else state.market_data_status
    required = (state.broker_status, market_status)
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


def _is_authoritative_stream_event(event: PaperRuntimeEvent) -> bool:
    event_type = event.event_type.strip().upper()
    if event_type in {
        "MARKET_DATA_QUOTE_RECEIVED",
        "QUOTE_RECEIVED",
        "MARKET_DATA_TRADE_RECEIVED",
        "TRADE_RECEIVED",
        "MARKET_DATA_SNAPSHOT_RECEIVED",
        "SNAPSHOT_RECEIVED",
    }:
        return True
    # The broker-neutral translator keeps this name for downstream mark
    # projections.  Its structured stream health proves it came from a
    # decoded TRADE payload rather than a paper/replay mark.
    return (
        event_type == "MARK_UPDATED"
        and event.health is not None
        and (event.health.streaming_status or "").upper() == "CONNECTED"
    )


def _successful_rest_observation(health) -> bool:
    return any(
        (value or "").upper() in {"AVAILABLE", "CONNECTED"}
        for value in (
            health.trading_rest_status,
            health.account_status,
            health.buying_power_status,
            health.positions_status,
            health.orders_status,
            health.balances_status,
            health.market_data_rest_status,
            health.historical_bars_status,
        )
    )


def _market_data_warning(value: str | None, *, rest_only: bool = False) -> bool:
    normalized = (value or "").upper()
    if rest_only:
        return "MARKET-DATA REST" in normalized or "HISTORICAL BAR" in normalized
    return "MARKET-DATA" in normalized or "MARKET DATA" in normalized


def _is_error(event_type: str) -> bool:
    if event_type == "STARTUP_VALIDATION_FAILED":
        return False
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
        subscription_symbols=state.subscription_symbols,
        ai_status=state.ai_status,
        risk_status=state.risk_status,
        persistence_status=state.persistence_status,
        last_error=state.last_error,
        last_warning=state.last_warning,
        last_heartbeat=state.last_heartbeat,
        last_market_data_event=state.last_market_data_event,
        market_data_stale_after_seconds=state.market_data_stale_after_seconds,
        connection_latency=state.connection_latency,
        reconnect_attempts=state.reconnect_attempts,
        degraded=state.degraded,
        healthy=state.healthy,
        capabilities=state.capabilities,
    )


__all__ = ["HealthProjection"]
