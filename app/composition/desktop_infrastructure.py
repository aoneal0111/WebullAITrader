"""Desktop scanner and paper-runtime infrastructure composition."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.execution_coordinator.runtime_context_input_source import (
    GFVDecisionSource,
    MarketQuoteSource,
    MarketStateSource,
    RuntimeContextConfiguration,
    TimestampSource,
)
from app.live_scanner.coordinator import LiveScannerCoordinator
from app.live_scanner.transport import ReceiveTransportAdapter
from app.momentum_scanner import MomentumScannerConfig
from app.operations.scanner_runtime import SnapshotResolver
from app.realtime_scanner.engine import RealtimeScannerEngine
from app.realtime_scanner.protocols import (
    ReferenceLoader,
    ReferenceSink,
    UniverseSelector,
)
from app.scanner_adapter.adapter import MarketEventScannerAdapter
from app.scanner_adapter.pipeline import MomentumScannerPipeline
from app.strategy_engine.order_intent_factory import (
    QuantityProvider,
    RequestIdProvider,
)

from .configured_paper_runtime import (
    create_configured_paper_runtime_dependencies,
)
from .paper_dependencies import PaperRuntimeDependencies


Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class DesktopScannerInfrastructure:
    """Concrete scanner objects assembled for the desktop runtime."""

    transport: ReceiveTransportAdapter
    pipeline: MomentumScannerPipeline
    engine: RealtimeScannerEngine
    coordinator: LiveScannerCoordinator


def create_desktop_scanner_infrastructure(
    *,
    market_data_client: Any,
    universe_service: UniverseSelector,
    reference_data_service: ReferenceLoader,
    scanner_adapter: MarketEventScannerAdapter,
    scanner_config: MomentumScannerConfig = MomentumScannerConfig(),
    reference_sink: ReferenceSink | None = None,
    clock: Clock | None = None,
    default_channels: Iterable[str] = (),
    maximum_events_per_cycle: int = 1000,
) -> DesktopScannerInfrastructure:
    """Assemble the live scanner infrastructure used by the desktop runtime."""

    transport = (
        market_data_client
        if isinstance(market_data_client, ReceiveTransportAdapter)
        else ReceiveTransportAdapter(market_data_client)
    )

    pipeline = MomentumScannerPipeline(
        scanner_adapter,
        scanner_config,
    )

    engine = RealtimeScannerEngine(
        universe_service,
        reference_data_service,
        pipeline,
        reference_sink=reference_sink,
        clock=clock,
    )

    coordinator = LiveScannerCoordinator(
        transport,
        engine,
        default_channels=default_channels,
        maximum_events_per_cycle=maximum_events_per_cycle,
    )

    return DesktopScannerInfrastructure(
        transport=transport,
        pipeline=pipeline,
        engine=engine,
        coordinator=coordinator,
    )


def create_desktop_paper_runtime_dependencies(
    *,
    scanner_infrastructure: DesktopScannerInfrastructure,
    snapshot_resolver: SnapshotResolver,
    quantity_provider: QuantityProvider,
    request_id_provider: RequestIdProvider,
    runtime_context_configuration: RuntimeContextConfiguration,
    timestamp_source: TimestampSource,
    market_state_source: MarketStateSource,
    market_quote_source: MarketQuoteSource,
    gfv_decision_source: GFVDecisionSource,
    clock: Clock,
    strategy_engine: Any | None = None,
    inference_adapter: Any | None = None,
    candidate_limit: int = 25,
    maximum_events_per_cycle: int = 1000,
) -> PaperRuntimeDependencies:
    """Create configured paper-runtime dependencies from desktop infrastructure."""

    return create_configured_paper_runtime_dependencies(
        scanner_coordinator=scanner_infrastructure.coordinator,
        snapshot_resolver=snapshot_resolver,
        quantity_provider=quantity_provider,
        request_id_provider=request_id_provider,
        runtime_context_configuration=runtime_context_configuration,
        timestamp_source=timestamp_source,
        market_state_source=market_state_source,
        market_quote_source=market_quote_source,
        gfv_decision_source=gfv_decision_source,
        clock=clock,
        strategy_engine=strategy_engine,
        inference_adapter=inference_adapter,
        candidate_limit=candidate_limit,
        maximum_events_per_cycle=maximum_events_per_cycle,
    )


__all__ = [
    "DesktopScannerInfrastructure",
    "create_desktop_paper_runtime_dependencies",
    "create_desktop_scanner_infrastructure",
]
