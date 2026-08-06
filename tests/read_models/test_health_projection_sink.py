from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.capabilities import (
    AssetCapability,
    CapabilityAvailability,
    CapabilityEntry,
    CapabilitySnapshot,
    SessionCapability,
)
from app.operations.runtime import (
    PaperRuntimeEvent,
    RuntimeHealthUpdate,
)
from app.operations_core import (
    ApplicationStateStore,
    HealthUpdated,
    OperationsBus,
)
from app.read_models.health import HealthState
from app.read_models.health_projection import HealthProjection


NOW = datetime(2026, 7, 30, 15, 0, tzinfo=UTC)


def event(
    sequence: int,
    event_type: str,
    *,
    message: str | None = None,
    health: RuntimeHealthUpdate | None = None,
    source: str = "paper-runtime",
) -> PaperRuntimeEvent:
    return PaperRuntimeEvent(
        sequence=sequence,
        timestamp=NOW + timedelta(seconds=sequence),
        event_type=event_type,
        message=message or event_type.replace("_", " ").title(),
        cycle=1,
        source=source,
        health=health,
    )


def make_healthy(projection: HealthProjection) -> None:
    projection(event(1, "STARTED"))
    projection(event(2, "BROKER_CONNECTED"))
    projection(event(3, "MARKET_DATA_CONNECTED"))
    projection(event(4, "AI_READY"))


def test_initial_and_startup_health_are_immutable_and_unknown_safe() -> None:
    projection = HealthProjection(OperationsBus())

    assert projection.snapshot == HealthState.initial()
    assert projection.snapshot.broker_status is None
    projection(event(1, "STARTING"))

    assert projection.snapshot.runtime_status == "STARTING"
    assert projection.snapshot.healthy is False
    assert projection.snapshot.degraded is False
    with pytest.raises(FrozenInstanceError):
        projection.snapshot.healthy = True  # type: ignore[misc]


def test_runtime_becomes_healthy_when_required_subsystems_are_ready() -> None:
    projection = HealthProjection(OperationsBus())

    make_healthy(projection)

    state = projection.snapshot
    assert state.runtime_status == "RUNNING"
    assert state.broker_status == "CONNECTED"
    assert state.market_data_status == "CONNECTED"
    assert state.ai_status == "READY"
    assert state.healthy is True
    assert state.degraded is False


def test_broker_disconnect_and_reconnect_transition_deterministically() -> None:
    projection = HealthProjection(OperationsBus())
    make_healthy(projection)

    projection(event(5, "BROKER_DISCONNECTED"))
    assert projection.snapshot.broker_status == "DISCONNECTED"
    assert projection.snapshot.degraded is True
    assert projection.snapshot.healthy is False

    projection(event(6, "BROKER_RECONNECT_ATTEMPT"))
    assert projection.snapshot.broker_status == "CONNECTING"
    assert projection.snapshot.reconnect_attempts == 1
    assert projection.snapshot.degraded is True

    projection(event(7, "BROKER_RECONNECTED"))
    assert projection.snapshot.broker_status == "CONNECTED"
    assert projection.snapshot.healthy is True
    assert projection.snapshot.degraded is False


def test_market_data_loss_degrades_an_otherwise_healthy_runtime() -> None:
    projection = HealthProjection(OperationsBus())
    make_healthy(projection)

    projection(event(5, "MARKET_DATA_LOST"))

    assert projection.snapshot.market_data_status == "DISCONNECTED"
    assert projection.snapshot.degraded is True
    assert projection.snapshot.healthy is False


def test_runtime_stop_is_intentionally_not_degraded() -> None:
    projection = HealthProjection(OperationsBus())
    make_healthy(projection)

    projection(event(5, "STOPPED"))

    assert projection.snapshot.runtime_status == "STOPPED"
    assert projection.snapshot.healthy is False
    assert projection.snapshot.degraded is False


def test_warning_updates_last_warning_without_inventing_status() -> None:
    projection = HealthProjection(OperationsBus())

    projection(
        event(
            1,
            "PERSISTENCE_WARNING",
            message="Checkpoint storage is nearing capacity.",
        )
    )

    assert (
        projection.snapshot.last_warning
        == "Checkpoint storage is nearing capacity."
    )
    assert projection.snapshot.persistence_status is None


def test_error_updates_last_error_and_degrades_affected_subsystem() -> None:
    projection = HealthProjection(OperationsBus())
    make_healthy(projection)

    projection(
        event(
            5,
            "BROKER_ERROR",
            message="Broker stream failed.",
        )
    )

    assert projection.snapshot.last_error == "Broker stream failed."
    assert projection.snapshot.broker_status == "ERROR"
    assert projection.snapshot.degraded is True
    assert projection.snapshot.healthy is False


def test_heartbeat_refreshes_timestamp_and_structured_latency() -> None:
    projection = HealthProjection(OperationsBus())
    heartbeat_at = NOW + timedelta(seconds=1)

    projection(
        event(
            1,
            "HEARTBEAT",
            health=RuntimeHealthUpdate(
                heartbeat_at=heartbeat_at,
                connection_latency=Decimal("12.5"),
                persistence_status="READY",
            ),
        )
    )

    assert projection.snapshot.last_heartbeat == heartbeat_at
    assert projection.snapshot.connection_latency == "12.5"
    assert projection.snapshot.persistence_status == "READY"

    projection(event(2, "HEARTBEAT"))
    assert projection.snapshot.last_heartbeat == NOW + timedelta(seconds=2)


def test_structured_health_event_can_establish_complete_health() -> None:
    projection = HealthProjection(OperationsBus())

    projection(
        event(
            1,
            "HEALTH_SNAPSHOT",
            health=RuntimeHealthUpdate(
                runtime_status="RUNNING",
                broker_status="CONNECTED",
                market_data_status="CONNECTED",
                ai_status="READY",
                risk_status="READY",
                persistence_status="READY",
                reconnect_attempts=3,
            ),
        )
    )

    assert projection.snapshot.healthy is True
    assert projection.snapshot.reconnect_attempts == 3


def test_duplicate_events_are_idempotent() -> None:
    bus = OperationsBus()
    updates = []
    bus.subscribe(HealthUpdated, updates.append)
    projection = HealthProjection(bus)
    reconnect = event(1, "BROKER_RECONNECT_ATTEMPT")

    projection(reconnect)
    projection(reconnect)

    assert projection.snapshot.reconnect_attempts == 1
    assert len(updates) == 1


def test_deterministic_replay_produces_identical_health_state() -> None:
    events = (
        event(1, "STARTED"),
        event(2, "BROKER_CONNECTED"),
        event(3, "MARKET_DATA_CONNECTED"),
        event(4, "AI_READY"),
        event(5, "HEARTBEAT"),
        event(6, "BROKER_DISCONNECTED"),
        event(7, "BROKER_RECONNECTED"),
    )
    results = []

    for _ in range(2):
        projection = HealthProjection(OperationsBus())
        for item in events:
            projection(item)
        results.append(projection.snapshot)

    assert results[0] == results[1]


def test_application_state_exposes_health_projection() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    projection = HealthProjection(bus)

    make_healthy(projection)

    assert store.snapshot().health_projection == projection.snapshot


def test_capability_refresh_replaces_snapshot_and_clears_pause_warning() -> None:
    projection = HealthProjection(OperationsBus())
    unavailable = CapabilitySnapshot(
        assets=(CapabilityEntry(
            AssetCapability.STOCKS,
            CapabilityAvailability.AVAILABLE,
        ),),
        sessions=(CapabilityEntry(
            SessionCapability.OVERNIGHT,
            CapabilityAvailability.SUBSCRIPTION_REQUIRED,
        ),),
    )
    available = CapabilitySnapshot(
        assets=unavailable.assets,
        sessions=(CapabilityEntry(
            SessionCapability.OVERNIGHT,
            CapabilityAvailability.AVAILABLE,
        ),),
    )

    projection(event(
        1,
        "MARKET_DATA_PROBE_COMPLETED",
        health=RuntimeHealthUpdate(
            scanner_status="PAUSED_UNTIL_PREMARKET",
            last_warning="Overnight market-data subscription unavailable.",
            capabilities=unavailable,
        ),
    ))
    assert projection.snapshot.capabilities == unavailable
    assert projection.snapshot.last_warning is not None

    projection(event(
        2,
        "MARKET_DATA_PROBE_COMPLETED",
        health=RuntimeHealthUpdate(
            scanner_status="WARMING",
            capabilities=available,
        ),
    ))
    assert projection.snapshot.capabilities == available
    assert projection.snapshot.last_warning is None


def test_successful_bars_and_quote_supersede_failed_startup_probe() -> None:
    projection = HealthProjection(OperationsBus())
    projection(event(1, "STARTED"))
    projection(event(2, "BROKER_CONNECTED"))
    projection(event(
        3,
        "MARKET_DATA_PROBE_COMPLETED",
        health=RuntimeHealthUpdate(
            market_data_status="FAILED",
            market_data_rest_status="UNAVAILABLE",
            streaming_status="UNAVAILABLE",
            historical_bars_status="UNAVAILABLE",
            quotes_status="UNAVAILABLE",
            scanner_status="DISABLED",
        ),
    ))
    projection(event(4, "HISTORICAL_BARS_LOADED"))
    projection(event(5, "MARKET_DATA_QUOTE_RECEIVED"))

    state = projection.snapshot
    assert state.market_data_rest_status == "CONNECTED"
    assert state.historical_bars_status == "AVAILABLE"
    assert state.streaming_status == "CONNECTED"
    assert state.subscription_status == "ACCEPTED"
    assert state.quotes_status == "AVAILABLE"
    assert state.market_data_status == "CONNECTED"
    assert state.healthy is True
    assert state.degraded is False


def test_optional_scanner_entitlement_does_not_fail_system_health() -> None:
    projection = HealthProjection(OperationsBus())
    projection(event(
        1,
        "HEALTH_SNAPSHOT",
        health=RuntimeHealthUpdate(
            runtime_status="RUNNING",
            broker_status="CONNECTED",
            market_data_status="CONNECTED",
            market_data_rest_status="CONNECTED",
            historical_bars_status="AVAILABLE",
            streaming_status="CONNECTED",
            subscription_status="ACCEPTED",
            quotes_status="AVAILABLE",
            scanner_status="CAPABILITY_PAUSED",
            entitlement_status="NOT_ENTITLED",
        ),
    ))

    assert projection.snapshot.healthy is True
    assert projection.snapshot.degraded is False
