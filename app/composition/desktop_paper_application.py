"""Top-level composition for the live Webull-backed PAPER desktop application."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.execution_coordinator.runtime_context_input_source import (
    GFVDecisionSource,
    MarketQuoteSource,
    MarketStateSource,
    RuntimeContextConfiguration,
    TimestampSource,
)
from app.momentum_scanner import MomentumScannerConfig
from app.operations.runtime import CheckpointSink, RuntimeEventSink
from app.operations_core import OperationsBus
from app.operations.scanner_runtime import ScannerRuntimeCycle, SnapshotResolver
from app.realtime_scanner.protocols import (
    ReferenceLoader,
    ReferenceSink,
    UniverseSelector,
)
from app.scanner_adapter.adapter import MarketEventScannerAdapter
from app.strategy_engine.order_intent_factory import (
    QuantityProvider,
    RequestIdProvider,
)
from app.webull.sdk_streaming_adapter import WebullMarketSubscription

from .desktop import DesktopComposition, create_desktop_composition
from app.webull.live_stream_factory import create_desktop_live_market_stream
from .desktop_runtime_bootstrap import (
    DesktopRuntimeBootstrap,
    create_desktop_runtime_bootstrap,
)
from .desktop_runtime_config import DesktopRuntimeConfiguration
from .runtime_mode import RuntimeMode
from .scanner_state_publisher import ScannerStatePublisher


Clock = Callable[[], datetime]


@dataclass(slots=True)
class DesktopPaperApplication:
    """Composed Webull-backed desktop PAPER application."""

    desktop: DesktopComposition
    runtime_bootstrap: DesktopRuntimeBootstrap
    market_data_client: Any

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        return self.desktop.close(timeout_seconds=timeout_seconds)


def create_desktop_paper_application(
    *,
    subscription: WebullMarketSubscription,
    universe_service: UniverseSelector,
    reference_data_service: ReferenceLoader,
    scanner_adapter: MarketEventScannerAdapter,
    snapshot_resolver: SnapshotResolver,
    quantity_provider: QuantityProvider,
    request_id_provider: RequestIdProvider,
    runtime_context_configuration: RuntimeContextConfiguration,
    timestamp_source: TimestampSource,
    market_state_source: MarketStateSource,
    market_quote_source: MarketQuoteSource,
    gfv_decision_source: GFVDecisionSource,
    clock: Clock,
    session_id: str,
    initial_cash: Decimal,
    scanner_config: MomentumScannerConfig = MomentumScannerConfig(),
    reference_sink: ReferenceSink | None = None,
    default_channels: Iterable[str] = (),
    maximum_events_per_cycle: int = 1000,
    candidate_limit: int = 25,
    strategy_engine: Any | None = None,
    inference_adapter: Any | None = None,
    event_sink: RuntimeEventSink | None = None,
    checkpoint_sink: CheckpointSink | None = None,
    interval_seconds: float = 1.0,
    environment: str = "PAPER",
    active_model: str = "Promoted model",
    scanner_cycle_sink: Callable[[ScannerRuntimeCycle], None] | None = None,
) -> DesktopPaperApplication:
    """
    Assemble the live market stream, paper runtime, and desktop application.

    Business authorities remain explicit injected dependencies.
    """

    market_data_client = create_desktop_live_market_stream(
        subscription=subscription,
    )

    bus = OperationsBus()
    scanner_state_publisher = ScannerStatePublisher(bus)

    runtime_bootstrap = create_desktop_runtime_bootstrap(
        market_data_client=market_data_client,
        universe_service=universe_service,
        reference_data_service=reference_data_service,
        scanner_adapter=scanner_adapter,
        snapshot_resolver=snapshot_resolver,
        quantity_provider=quantity_provider,
        request_id_provider=request_id_provider,
        runtime_context_configuration=runtime_context_configuration,
        timestamp_source=timestamp_source,
        market_state_source=market_state_source,
        market_quote_source=market_quote_source,
        gfv_decision_source=gfv_decision_source,
        clock=clock,
        session_id=session_id,
        initial_cash=initial_cash,
        scanner_config=scanner_config,
        reference_sink=reference_sink,
        default_channels=default_channels,
        maximum_events_per_cycle=maximum_events_per_cycle,
        candidate_limit=candidate_limit,
        strategy_engine=strategy_engine,
        inference_adapter=inference_adapter,
        event_sink=event_sink,
        checkpoint_sink=checkpoint_sink,
        interval_seconds=interval_seconds,
        environment=environment,
        active_model=active_model,
        scanner_cycle_sink=scanner_cycle_sink,
        scanner_snapshot_sink=scanner_state_publisher,
    )

    desktop = create_desktop_composition(
        driver_factory=runtime_bootstrap.driver_factory,
        bus=bus,
        configuration=DesktopRuntimeConfiguration(
            runtime_mode=RuntimeMode.PAPER,
        ),
    )

    return DesktopPaperApplication(
        desktop=desktop,
        runtime_bootstrap=runtime_bootstrap,
        market_data_client=market_data_client,
    )


def create_desktop_paper_composition(
    *,
    driver_factory: Callable[[], object],
) -> DesktopComposition:
    """Compose the desktop application around an explicit paper driver factory."""

    if not callable(driver_factory):
        raise TypeError("driver_factory must be callable")

    return create_desktop_composition(
        driver_factory=driver_factory,
        configuration=DesktopRuntimeConfiguration(
            runtime_mode=RuntimeMode.PAPER,
        ),
    )


__all__ = [
    "DesktopPaperApplication",
    "create_desktop_paper_application",
    "create_desktop_paper_composition",
]

