from __future__ import annotations

from dataclasses import dataclass

from app.operations_core import ApplicationStateStore, OperationsBus
from app.services import RuntimeService, SimulatedPaperRuntimeDriver


@dataclass(frozen=True, slots=True)
class DesktopComposition:
    """Fully composed application dependencies for the desktop entry point."""

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


def create_desktop_composition() -> DesktopComposition:
    """Construct the current desktop application dependency graph.

    This initial composition intentionally retains the simulated runtime.
    Replacing the driver belongs to Bravo 2 and will not require GUI changes.
    """

    bus = OperationsBus()
    state_store = ApplicationStateStore(bus)

    runtime_service = RuntimeService(
        bus,
        lambda: SimulatedPaperRuntimeDriver(
            interval_seconds=1.0,
            environment="PAPER",
            active_model="Promoted model",
        ),
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
