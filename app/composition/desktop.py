from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.operations_core import (
    ApplicationStateStore,
    OperationsBus,
)
from app.services import RuntimeService

from .desktop_runtime import create_desktop_runtime_service
from .desktop_runtime_config import DesktopRuntimeConfiguration


@dataclass(slots=True)
class DesktopComposition:
    bus: OperationsBus
    state_store: ApplicationStateStore
    runtime_service: RuntimeService

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        """Close composed resources in lifecycle order."""

        runtime_stopped = self.runtime_service.close(
            timeout_seconds=timeout_seconds
        )
        self.state_store.close()
        return runtime_stopped


def create_desktop_composition(
    driver_factory: Callable[[], object] | None = None,
    *,
    configuration: DesktopRuntimeConfiguration = DesktopRuntimeConfiguration(),
) -> DesktopComposition:
    """Construct the desktop application dependency graph."""

    bus = OperationsBus()
    state_store = ApplicationStateStore(bus)

    runtime_service = create_desktop_runtime_service(
        bus,
        driver_factory=driver_factory,
        runtime_mode=configuration.runtime_mode,
    )

    return DesktopComposition(
        bus=bus,
        state_store=state_store,
        runtime_service=runtime_service,
    )


__all__ = [
    "DesktopComposition",
    "create_desktop_composition",
]
