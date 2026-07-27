from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.operations_core import (
    ApplicationStateStore,
    OperationsBus,
)
from app.order_cancellation import OrderCancellationRuntime
from app.order_placement import OrderPlacementRuntime
from app.services import RuntimeService, TradingService

from .desktop_runtime import create_desktop_runtime_service
from .desktop_runtime_config import DesktopRuntimeConfiguration


@dataclass(slots=True)
class DesktopComposition:
    bus: OperationsBus
    state_store: ApplicationStateStore
    runtime_service: RuntimeService
    trading_service: TradingService | None = None

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
    placement_runtime: OrderPlacementRuntime | None = None,
    cancellation_runtime: OrderCancellationRuntime | None = None,
) -> DesktopComposition:
    """Construct the desktop application dependency graph."""

    bus = OperationsBus()
    state_store = ApplicationStateStore(bus)

    runtime_service = create_desktop_runtime_service(
        bus,
        driver_factory=driver_factory,
        runtime_mode=configuration.runtime_mode,
    )

    if (placement_runtime is None) != (cancellation_runtime is None):
        raise ValueError(
            "placement_runtime and cancellation_runtime must be provided together"
        )

    trading_service = (
        TradingService(placement_runtime, cancellation_runtime)
        if placement_runtime is not None and cancellation_runtime is not None
        else None
    )

    return DesktopComposition(
        bus=bus,
        state_store=state_store,
        runtime_service=runtime_service,
        trading_service=trading_service,
    )


__all__ = [
    "DesktopComposition",
    "create_desktop_composition",
]
