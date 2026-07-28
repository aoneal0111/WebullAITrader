from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.operations_core import (
    ApplicationStateStore,
    OperationsBus,
    OperationsEvent,
    RuntimeFailed,
    RuntimePhase,
    RuntimeStarted,
    RuntimeStarting,
    RuntimeStopped,
)


def test_event_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        OperationsEvent(
            occurred_at=datetime(2026, 7, 20, 12, 0),
        )


def test_bus_delivers_events_in_subscription_order() -> None:
    bus = OperationsBus()
    received: list[str] = []

    bus.subscribe(
        RuntimeStarted,
        lambda event: received.append("first"),
    )
    bus.subscribe(
        RuntimeStarted,
        lambda event: received.append("second"),
    )

    bus.publish(RuntimeStarted(active_model="model-v1"))

    assert received == ["first", "second"]


def test_bus_preserves_global_order_across_base_and_specific_types() -> None:
    bus = OperationsBus()
    received: list[str] = []

    bus.subscribe(
        OperationsEvent,
        lambda event: received.append("base-first"),
    )
    bus.subscribe(
        RuntimeStarted,
        lambda event: received.append("specific-second"),
    )
    bus.subscribe(
        OperationsEvent,
        lambda event: received.append("base-third"),
    )

    bus.publish(RuntimeStarted(active_model="model-v1"))

    assert received == [
        "base-first",
        "specific-second",
        "base-third",
    ]


def test_bus_unsubscribe_stops_delivery() -> None:
    bus = OperationsBus()
    received: list[RuntimeStarted] = []

    subscription = bus.subscribe(RuntimeStarted, received.append)

    assert bus.unsubscribe(subscription) is True
    assert bus.unsubscribe(subscription) is False

    bus.publish(RuntimeStarted(active_model="model-v1"))

    assert received == []


def test_base_event_subscription_receives_subclasses() -> None:
    bus = OperationsBus()
    received: list[OperationsEvent] = []

    bus.subscribe(OperationsEvent, received.append)
    event = RuntimeStarting(environment="PAPER")

    bus.publish(event)

    assert received == [event]


def test_state_store_reduces_runtime_lifecycle() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)

    bus.publish(RuntimeStarting(environment="PAPER"))
    assert store.snapshot().runtime.phase is RuntimePhase.STARTING

    bus.publish(
        RuntimeStarted(
            environment="PAPER",
            active_model="momentum-v17",
        )
    )

    running = store.snapshot()

    assert running.runtime.phase is RuntimePhase.RUNNING
    assert running.runtime.broker_status == "Connected"
    assert running.runtime.market_feed_status == "Healthy"
    assert running.runtime.active_model == "momentum-v17"

    bus.publish(
        RuntimeStopped(
            reason="Session complete",
            cycles_completed=42,
        )
    )

    stopped = store.snapshot()

    assert stopped.runtime.phase is RuntimePhase.STOPPED
    assert stopped.runtime.cycles_completed == 42
    assert stopped.revision == 3
    assert len(stopped.timeline) == 3


def test_runtime_failure_is_visible_in_state_and_timeline() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)

    bus.publish(RuntimeFailed(error_message="market feed unavailable"))

    snapshot = store.snapshot()

    assert snapshot.runtime.phase is RuntimePhase.FAILED
    assert snapshot.runtime.last_error == "market feed unavailable"
    assert snapshot.timeline[-1].event_type == "RuntimeFailed"
    assert "market feed unavailable" in snapshot.timeline[-1].message


def test_state_listener_receives_initial_and_updated_snapshots() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    revisions: list[int] = []

    listener_id = store.subscribe(
        lambda state: revisions.append(state.revision)
    )

    bus.publish(RuntimeStarting())

    assert revisions == [0, 1]
    assert store.unsubscribe(listener_id) is True


def test_timeline_is_bounded() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus, timeline_limit=2)

    bus.publish(RuntimeStarting())
    bus.publish(RuntimeStarted(active_model="model-v1"))
    bus.publish(RuntimeStopped(cycles_completed=3))

    snapshot = store.snapshot()

    assert len(snapshot.timeline) == 2
    assert snapshot.timeline[0].event_type == "RuntimeStarted"
    assert snapshot.timeline[1].event_type == "RuntimeStopped"


def test_stopped_event_rejects_negative_cycle_count() -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        RuntimeStopped(cycles_completed=-1)


def test_event_defaults_to_aware_utc_timestamp() -> None:
    event = RuntimeStarting()

    assert event.occurred_at.tzinfo is timezone.utc


def test_runtime_cycle_event_updates_state_without_flooding_timeline() -> None:
    from app.operations_core import RuntimeCycleCompleted

    bus = OperationsBus()
    store = ApplicationStateStore(bus)

    bus.publish(RuntimeStarting())
    bus.publish(RuntimeStarted(active_model="model-v1"))

    timeline_length = len(store.snapshot().timeline)

    bus.publish(RuntimeCycleCompleted(cycle_count=1))
    bus.publish(RuntimeCycleCompleted(cycle_count=2))
    bus.publish(RuntimeCycleCompleted(cycle_count=3))

    snapshot = store.snapshot()

    assert snapshot.runtime.cycles_completed == 3
    assert len(snapshot.timeline) == timeline_length


def test_runtime_cycle_event_rejects_negative_count() -> None:
    from app.operations_core import RuntimeCycleCompleted

    with pytest.raises(ValueError, match="nonnegative"):
        RuntimeCycleCompleted(cycle_count=-1)
