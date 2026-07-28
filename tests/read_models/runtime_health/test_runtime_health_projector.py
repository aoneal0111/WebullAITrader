from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.operations_core import (
    OperationsBus,
    OperationsEvent,
    PaperRuntimeSnapshot,
    PaperRuntimeUpdated,
    RuntimeCycleCompleted,
    RuntimeFailed,
    RuntimeStarted,
    RuntimeStarting,
    RuntimeStopped,
    RuntimeStopping,
)
from app.read_models.runtime_health import (
    OverallHealth,
    RuntimeHealthProjector,
    RuntimeHealthSnapshot,
)


NOW = datetime(2026, 7, 28, 15, 0, tzinfo=timezone.utc)


def test_projector_starts_with_unknown_immutable_snapshot() -> None:
    bus = OperationsBus()
    projector = RuntimeHealthProjector(bus)
    try:
        assert projector.snapshot() == RuntimeHealthSnapshot.initial()
    finally:
        projector.close()


def test_lifecycle_events_project_running_health_and_cycles() -> None:
    bus = OperationsBus()
    projector = RuntimeHealthProjector(bus)
    try:
        bus.publish(RuntimeStarting(occurred_at=NOW))
        starting = projector.snapshot()
        assert starting.overall_health is OverallHealth.DEGRADED
        assert starting.runtime_state == "STARTING"
        assert starting.broker.status == "Connecting"
        assert starting.warnings == ("Runtime startup in progress.",)

        bus.publish(
            RuntimeStarted(
                occurred_at=NOW + timedelta(seconds=1),
                active_model="atlas",
            )
        )
        running = projector.snapshot()
        assert running.overall_health is OverallHealth.HEALTHY
        assert running.runtime_state == "RUNNING"
        assert running.scanner.status == "Running"
        assert running.market_data.status == "Healthy"
        assert running.current_cycle.value == 1

        completed_at = NOW + timedelta(seconds=2)
        bus.publish(
            RuntimeCycleCompleted(
                occurred_at=completed_at,
                cycle_count=4,
            )
        )
        completed = projector.snapshot()
        assert completed.current_cycle.value == 5
        assert completed.last_completed_cycle.value == 4
        assert completed.last_update_time == completed_at
    finally:
        projector.close()


def test_stopping_and_stopped_states_preserve_completed_cycle() -> None:
    bus = OperationsBus()
    projector = RuntimeHealthProjector(bus)
    try:
        bus.publish(RuntimeCycleCompleted(cycle_count=3, occurred_at=NOW))
        bus.publish(
            RuntimeStopping(
                reason="Maintenance requested.",
                occurred_at=NOW,
            )
        )
        stopping = projector.snapshot()
        assert stopping.overall_health is OverallHealth.DEGRADED
        assert stopping.runtime_state == "STOPPING"
        assert stopping.current_cycle.value == 3
        assert stopping.warnings == ("Maintenance requested.",)

        bus.publish(
            RuntimeStopped(
                cycles_completed=3,
                occurred_at=NOW,
            )
        )
        stopped = projector.snapshot()
        assert stopped.overall_health is OverallHealth.UNKNOWN
        assert stopped.runtime_state == "STOPPED"
        assert stopped.broker.status == "Disconnected"
        assert stopped.current_cycle.value == 3
        assert stopped.last_completed_cycle.value == 3
        assert stopped.warnings == ()
    finally:
        projector.close()


def test_failure_projects_errors_without_marking_bus_unhealthy() -> None:
    bus = OperationsBus()
    projector = RuntimeHealthProjector(bus)
    try:
        bus.publish(
            RuntimeFailed(
                error_message="feed unavailable",
                occurred_at=NOW,
            )
        )

        snapshot = projector.snapshot()
        assert snapshot.overall_health is OverallHealth.UNHEALTHY
        assert snapshot.runtime_state == "FAILED"
        assert snapshot.errors == ("feed unavailable",)
        assert snapshot.market_data.health is OverallHealth.UNHEALTHY
        assert snapshot.operations_bus.health is OverallHealth.HEALTHY
    finally:
        projector.close()


def test_paper_runtime_update_advances_completed_cycle() -> None:
    bus = OperationsBus()
    projector = RuntimeHealthProjector(bus)
    snapshot = PaperRuntimeSnapshot(
        cycle=6,
        timestamp=NOW,
        session_id="paper-1",
        symbols=("AAPL",),
        decisions_processed=1,
        orders_attempted=0,
        orders_filled=0,
        orders_rejected=0,
        orders_not_filled=0,
        decisions_skipped=1,
        winning_fills=0,
        losing_fills=0,
        breakeven_fills=0,
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        current_equity=Decimal("10000"),
        peak_equity=Decimal("10000"),
        current_drawdown=Decimal("0"),
        win_rate=Decimal("0"),
        total_return=Decimal("0"),
        maximum_drawdown=Decimal("0"),
    )
    try:
        bus.publish(
            PaperRuntimeUpdated(
                snapshot=snapshot,
                occurred_at=NOW,
            )
        )

        health = projector.snapshot()
        assert health.current_cycle.value == 7
        assert health.last_completed_cycle.value == 6
        assert health.runtime_state == "RUNNING"
    finally:
        projector.close()


def test_any_delivered_event_proves_operations_bus_health() -> None:
    bus = OperationsBus()
    projector = RuntimeHealthProjector(bus)
    try:
        bus.publish(OperationsEvent(occurred_at=NOW, source="test"))

        snapshot = projector.snapshot()
        assert snapshot.overall_health is OverallHealth.UNKNOWN
        assert snapshot.operations_bus.status == "Receiving events"
        assert snapshot.operations_bus.updated_at == NOW
        assert snapshot.last_update_time == NOW
    finally:
        projector.close()


def test_close_unsubscribes_and_is_idempotent() -> None:
    bus = OperationsBus()
    projector = RuntimeHealthProjector(bus)

    assert bus.subscription_count == 1
    projector.close()
    projector.close()

    assert bus.subscription_count == 0
