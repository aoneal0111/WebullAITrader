"""Desktop runtime-service composition helpers."""

from __future__ import annotations

from collections.abc import Callable
from itertools import count

from app.operations_core import OperationsBus
from app.read_models.health_projection import HealthProjection
from app.services import RuntimeService, SimulatedPaperRuntimeDriver

from .broker_account_projection import create_broker_account_publisher
from .desktop_broker_runtime import create_configured_desktop_broker_driver
from .runtime_mode import RuntimeMode


def _broker_driver_factory(
    bus: OperationsBus,
) -> Callable[[], object]:
    health_projection = HealthProjection(bus)
    account_publisher = create_broker_account_publisher(bus)
    session_numbers = count(1)

    def create_driver() -> object:
        return create_configured_desktop_broker_driver(
            event_sink=health_projection,
            account_snapshot_sink=account_publisher,
            source=f"desktop-broker-runtime:{next(session_numbers)}",
        )

    return create_driver


def _simulated_driver_factory() -> SimulatedPaperRuntimeDriver:
    return SimulatedPaperRuntimeDriver(
        interval_seconds=1.0,
        environment="PAPER",
        active_model="Promoted model",
    )


def _resolve_driver_factory(
    *,
    bus: OperationsBus,
    runtime_mode: RuntimeMode,
    driver_factory: Callable[[], object] | None,
) -> Callable[[], object]:
    if driver_factory is not None:
        return driver_factory

    if runtime_mode is RuntimeMode.SIMULATED:
        return _simulated_driver_factory

    return _broker_driver_factory(bus)


def create_desktop_runtime_service(
    bus: OperationsBus,
    driver_factory: Callable[[], object] | None = None,
    *,
    runtime_mode: RuntimeMode = RuntimeMode.PAPER,
) -> RuntimeService:
    """Create the runtime service used by the desktop composition root."""

    return RuntimeService(
        bus,
        _resolve_driver_factory(
            bus=bus,
            runtime_mode=runtime_mode,
            driver_factory=driver_factory,
        ),
    )


__all__ = [
    "create_desktop_runtime_service",
]
