"""Desktop runtime-service composition helpers."""

from __future__ import annotations

from collections.abc import Callable

from app.operations_core import OperationsBus
from app.services import RuntimeService, SimulatedPaperRuntimeDriver


def _default_driver_factory() -> SimulatedPaperRuntimeDriver:
    return SimulatedPaperRuntimeDriver(
        interval_seconds=1.0,
        environment="PAPER",
        active_model="Promoted model",
    )


def create_desktop_runtime_service(
    bus: OperationsBus,
    driver_factory: Callable[[], object] | None = None,
) -> RuntimeService:
    """Create the runtime service used by the desktop composition root."""

    return RuntimeService(
        bus,
        driver_factory or _default_driver_factory,
    )


__all__ = [
    "create_desktop_runtime_service",
]
