"""Desktop runtime-service composition helpers."""

from __future__ import annotations

from collections.abc import Callable

from app.operations_core import OperationsBus
from app.services import RuntimeService, SimulatedPaperRuntimeDriver

from .runtime_mode import RuntimeMode


def _default_driver_factory() -> SimulatedPaperRuntimeDriver:
    return SimulatedPaperRuntimeDriver(
        interval_seconds=1.0,
        environment="PAPER",
        active_model="Promoted model",
    )


def _resolve_driver_factory(
    *,
    runtime_mode: RuntimeMode,
    driver_factory: Callable[[], object] | None,
) -> Callable[[], object]:
    if driver_factory is not None:
        return driver_factory

    if runtime_mode is RuntimeMode.SIMULATED:
        return _default_driver_factory

    raise ValueError(
        "PAPER desktop runtime requires an explicit real driver factory"
    )


def create_desktop_runtime_service(
    bus: OperationsBus,
    driver_factory: Callable[[], object] | None = None,
    *,
    runtime_mode: RuntimeMode = RuntimeMode.SIMULATED,
) -> RuntimeService:
    """Create the runtime service used by the desktop composition root."""

    return RuntimeService(
        bus,
        _resolve_driver_factory(
            runtime_mode=runtime_mode,
            driver_factory=driver_factory,
        ),
    )


__all__ = [
    "create_desktop_runtime_service",
]
