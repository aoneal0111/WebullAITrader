"""Desktop runtime-service composition helpers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from itertools import count

from app.configuration import load_configuration
from app.operations.runtime import RuntimeEventSink
from app.operations_core import OperationsBus
from app.services import RuntimeService, SimulatedPaperRuntimeDriver

from .broker_account_projection import create_broker_account_publisher
from .desktop_broker_runtime import create_configured_desktop_broker_driver
from .runtime_projection_pipeline import create_runtime_projection_pipeline
from .runtime_mode import RuntimeMode


def _broker_driver_factory(
    bus: OperationsBus,
    *,
    event_sink: RuntimeEventSink | None = None,
    market_event_observer: Callable[[object], object] | None = None,
) -> Callable[[], object]:
    account_publisher = create_broker_account_publisher(bus)
    session_numbers = count(1)

    def create_driver() -> object:
        configuration = load_configuration()
        resolved_event_sink = event_sink
        if resolved_event_sink is None:
            resolved_event_sink = create_runtime_projection_pipeline(
                operations_bus=bus,
                account_id=configuration.account_id or "broker",
                watchlist_stale_after=timedelta(
                    seconds=(
                        configuration.maximum_market_data_age_seconds
                    )
                ),
            ).sink
        return create_configured_desktop_broker_driver(
            event_sink=resolved_event_sink,
            account_snapshot_sink=account_publisher,
            configuration_loader=lambda: configuration,
            market_event_observer=market_event_observer,
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
    event_sink: RuntimeEventSink | None,
    market_event_observer: Callable[[object], object] | None,
) -> Callable[[], object]:
    if driver_factory is not None:
        return driver_factory

    if runtime_mode is RuntimeMode.SIMULATED:
        return _simulated_driver_factory

    return _broker_driver_factory(
        bus,
        event_sink=event_sink,
        market_event_observer=market_event_observer,
    )


def create_desktop_runtime_service(
    bus: OperationsBus,
    driver_factory: Callable[[], object] | None = None,
    *,
    runtime_mode: RuntimeMode = RuntimeMode.PAPER,
    event_sink: RuntimeEventSink | None = None,
    market_event_observer: Callable[[object], object] | None = None,
) -> RuntimeService:
    """Create the runtime service used by the desktop composition root."""

    return RuntimeService(
        bus,
        _resolve_driver_factory(
            bus=bus,
            runtime_mode=runtime_mode,
            driver_factory=driver_factory,
            event_sink=event_sink,
            market_event_observer=market_event_observer,
        ),
    )


__all__ = [
    "create_desktop_runtime_service",
]
