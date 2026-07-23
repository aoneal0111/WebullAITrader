"""Desktop runtime-service composition helpers."""

from __future__ import annotations

from app.operations_core import OperationsBus
from app.services import RuntimeService, SimulatedPaperRuntimeDriver


def create_desktop_runtime_service(
    bus: OperationsBus,
) -> RuntimeService:
    """Create the runtime service used by the desktop composition root."""

    return RuntimeService(
        bus,
        lambda: SimulatedPaperRuntimeDriver(
            interval_seconds=1.0,
            environment="PAPER",
            active_model="Promoted model",
        ),
    )


__all__ = [
    "create_desktop_runtime_service",
]
