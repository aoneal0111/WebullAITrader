from __future__ import annotations

from dataclasses import replace
from threading import RLock

from app.operations_core import (
    OperationsBus,
    OperationsEvent,
    PaperRuntimeUpdated,
    RuntimeCycleCompleted,
    RuntimeFailed,
    RuntimeStarted,
    RuntimeStarting,
    RuntimeStopped,
    RuntimeStopping,
    Subscription,
)

from .models import (
    HealthMetric,
    OverallHealth,
    RuntimeHealthSnapshot,
    SubsystemHealth,
)


class RuntimeHealthProjector:
    """Project OperationsBus lifecycle events into immutable health state."""

    def __init__(self, bus: OperationsBus) -> None:
        if not isinstance(bus, OperationsBus):
            raise TypeError("bus must be an OperationsBus")
        self._bus = bus
        self._lock = RLock()
        self._snapshot = RuntimeHealthSnapshot.initial()
        self._subscription: Subscription | None = bus.subscribe(
            OperationsEvent,
            self._handle_event,
        )

    def snapshot(self) -> RuntimeHealthSnapshot:
        with self._lock:
            return self._snapshot

    def close(self) -> None:
        subscription = self._subscription
        if subscription is not None:
            self._bus.unsubscribe(subscription)
            self._subscription = None

    def _handle_event(self, event: OperationsEvent) -> None:
        with self._lock:
            current = self._mark_bus_healthy(self._snapshot, event)
            self._snapshot = self._reduce(current, event)

    @staticmethod
    def _mark_bus_healthy(
        current: RuntimeHealthSnapshot,
        event: OperationsEvent,
    ) -> RuntimeHealthSnapshot:
        return replace(
            current,
            operations_bus=_subsystem(
                "operations_bus",
                OverallHealth.HEALTHY,
                "Receiving events",
                event,
            ),
            last_update_time=event.occurred_at,
        )

    @staticmethod
    def _reduce(
        current: RuntimeHealthSnapshot,
        event: OperationsEvent,
    ) -> RuntimeHealthSnapshot:
        if isinstance(event, RuntimeStarting):
            return _lifecycle_snapshot(
                current,
                event,
                overall_health=OverallHealth.DEGRADED,
                runtime_state="STARTING",
                subsystem_health=OverallHealth.DEGRADED,
                broker_status="Connecting",
                scanner_status="Starting",
                market_data_status="Starting",
                current_cycle=0,
                last_completed_cycle=0,
                warnings=("Runtime startup in progress.",),
            )
        if isinstance(event, RuntimeStarted):
            return _lifecycle_snapshot(
                current,
                event,
                overall_health=OverallHealth.HEALTHY,
                runtime_state="RUNNING",
                subsystem_health=OverallHealth.HEALTHY,
                broker_status="Connected",
                scanner_status="Running",
                market_data_status="Healthy",
                current_cycle=1,
                last_completed_cycle=0,
            )
        if isinstance(event, (RuntimeCycleCompleted, PaperRuntimeUpdated)):
            cycle = (
                event.cycle_count
                if isinstance(event, RuntimeCycleCompleted)
                else event.snapshot.cycle
            )
            return replace(
                current,
                overall_health=OverallHealth.HEALTHY,
                runtime_state="RUNNING",
                broker=_subsystem(
                    "broker",
                    OverallHealth.HEALTHY,
                    "Connected",
                    event,
                ),
                scanner=_subsystem(
                    "scanner",
                    OverallHealth.HEALTHY,
                    "Running",
                    event,
                ),
                market_data=_subsystem(
                    "market_data",
                    OverallHealth.HEALTHY,
                    "Healthy",
                    event,
                ),
                current_cycle=_metric("current_cycle", cycle + 1, event),
                last_completed_cycle=_metric(
                    "last_completed_cycle",
                    cycle,
                    event,
                ),
                warnings=(),
                errors=(),
            )
        if isinstance(event, RuntimeStopping):
            return _lifecycle_snapshot(
                current,
                event,
                overall_health=OverallHealth.DEGRADED,
                runtime_state="STOPPING",
                subsystem_health=OverallHealth.DEGRADED,
                broker_status="Disconnecting",
                scanner_status="Stopping",
                market_data_status="Stopping",
                current_cycle=current.last_completed_cycle.value,
                last_completed_cycle=current.last_completed_cycle.value,
                warnings=(event.reason,),
            )
        if isinstance(event, RuntimeStopped):
            return _lifecycle_snapshot(
                current,
                event,
                overall_health=OverallHealth.UNKNOWN,
                runtime_state="STOPPED",
                subsystem_health=OverallHealth.UNKNOWN,
                broker_status="Disconnected",
                scanner_status="Stopped",
                market_data_status="Idle",
                current_cycle=event.cycles_completed,
                last_completed_cycle=event.cycles_completed,
            )
        if isinstance(event, RuntimeFailed):
            return _lifecycle_snapshot(
                current,
                event,
                overall_health=OverallHealth.UNHEALTHY,
                runtime_state="FAILED",
                subsystem_health=OverallHealth.UNHEALTHY,
                broker_status="Disconnected",
                scanner_status="Error",
                market_data_status="Error",
                current_cycle=current.last_completed_cycle.value,
                last_completed_cycle=current.last_completed_cycle.value,
                errors=(event.error_message,),
            )
        return current


def _lifecycle_snapshot(
    current: RuntimeHealthSnapshot,
    event: OperationsEvent,
    *,
    overall_health: OverallHealth,
    runtime_state: str,
    subsystem_health: OverallHealth,
    broker_status: str,
    scanner_status: str,
    market_data_status: str,
    current_cycle: int,
    last_completed_cycle: int,
    warnings: tuple[str, ...] = (),
    errors: tuple[str, ...] = (),
) -> RuntimeHealthSnapshot:
    return RuntimeHealthSnapshot(
        overall_health=overall_health,
        runtime_state=runtime_state,
        broker=_subsystem(
            "broker",
            subsystem_health,
            broker_status,
            event,
        ),
        scanner=_subsystem(
            "scanner",
            subsystem_health,
            scanner_status,
            event,
        ),
        market_data=_subsystem(
            "market_data",
            subsystem_health,
            market_data_status,
            event,
        ),
        operations_bus=current.operations_bus,
        current_cycle=_metric("current_cycle", current_cycle, event),
        last_completed_cycle=_metric(
            "last_completed_cycle",
            last_completed_cycle,
            event,
        ),
        last_update_time=event.occurred_at,
        warnings=warnings,
        errors=errors,
    )


def _subsystem(
    name: str,
    health: OverallHealth,
    status: str,
    event: OperationsEvent,
) -> SubsystemHealth:
    return SubsystemHealth(
        name=name,
        health=health,
        status=status,
        updated_at=event.occurred_at,
    )


def _metric(
    name: str,
    value: int,
    event: OperationsEvent,
) -> HealthMetric:
    return HealthMetric(
        name=name,
        value=value,
        updated_at=event.occurred_at,
    )
