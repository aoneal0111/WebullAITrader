"""Top-level composition for a configured desktop paper runtime."""

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
from app.operations.runtime import (
    CheckpointSink,
    PaperRuntimeCycleResult,
    RuntimeEventSink,
)
from app.operations_core import OperationsBus
from app.read_models.order_projection import OrderProjection
from app.read_models.position_projection import PositionProjection
from app.read_models.timeline_projection import TimelineProjection
from app.read_models.decision_projection import DecisionProjection
from app.read_models.portfolio_projection import PortfolioProjection
from app.operations.scanner_runtime import SnapshotResolver
from app.realtime_scanner.protocols import (
    ReferenceLoader,
    ReferenceSink,
    UniverseSelector,
)
from app.scanner_adapter.adapter import MarketEventScannerAdapter
from app.services.runtime_service import DriverFactory
from app.strategy_engine.order_intent_factory import (
    QuantityProvider,
    RequestIdProvider,
)

from .desktop_infrastructure import (
    DesktopScannerInfrastructure,
    create_desktop_paper_runtime_dependencies,
    create_desktop_scanner_infrastructure,
)
from .paper_dependencies import PaperRuntimeDependencies
from .paper_runtime_composition import create_paper_runtime_driver_factory
from .runtime_event_sink import CompositeRuntimeEventSink


Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class DesktopRuntimeBootstrap:
    """Objects assembled for one configured desktop paper runtime."""

    scanner_infrastructure: DesktopScannerInfrastructure
    runtime_dependencies: PaperRuntimeDependencies
    driver_factory: DriverFactory
    order_projection: OrderProjection | None = None
    position_projection: PositionProjection | None = None
    timeline_projection: TimelineProjection | None = None
    decision_projection: DecisionProjection | None = None
    portfolio_projection: PortfolioProjection | None = None


def create_desktop_runtime_bootstrap(
    *,
    market_data_client: Any,
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
    event_sinks: Iterable[RuntimeEventSink | None] = (),
    operations_bus: OperationsBus | None = None,
    timeline_history_limit: int = 500,
    checkpoint_sink: CheckpointSink | None = None,
    runtime_result_sink: Callable[[PaperRuntimeCycleResult], None] | None = None,
    interval_seconds: float = 1.0,
    environment: str = "PAPER",
    active_model: str = "Promoted model",
) -> DesktopRuntimeBootstrap:
    """
    Assemble a configured desktop paper runtime from application authorities.

    This composition function wires existing services only. It does not invent
    quantities, identifiers, market data, account state, or policy decisions.
    """

    scanner_infrastructure = create_desktop_scanner_infrastructure(
        market_data_client=market_data_client,
        universe_service=universe_service,
        reference_data_service=reference_data_service,
        scanner_adapter=scanner_adapter,
        scanner_config=scanner_config,
        reference_sink=reference_sink,
        clock=clock,
        default_channels=default_channels,
        maximum_events_per_cycle=maximum_events_per_cycle,
    )

    runtime_dependencies = create_desktop_paper_runtime_dependencies(
        scanner_infrastructure=scanner_infrastructure,
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

    order_projection = (
        OrderProjection(operations_bus)
        if operations_bus is not None
        else None
    )
    position_projection = (
        PositionProjection(
            operations_bus,
            account_id=session_id,
        )
        if operations_bus is not None
        else None
    )
    timeline_projection = (
        TimelineProjection(
            operations_bus,
            maximum_entries=timeline_history_limit,
        )
        if operations_bus is not None
        else None
    )
    decision_projection = (
        DecisionProjection(operations_bus)
        if operations_bus is not None
        else None
    )
    portfolio_projection = (
        PortfolioProjection(
            operations_bus,
            position_projection=position_projection,
            order_projection=order_projection,
        )
        if (
            operations_bus is not None
            and position_projection is not None
            and order_projection is not None
        )
        else None
    )
    composed_event_sinks = (
        order_projection,
        position_projection,
        portfolio_projection,
        timeline_projection,
        decision_projection,
        *tuple(event_sinks),
    )
    resolved_event_sink: RuntimeEventSink | None = event_sink
    if any(sink is not None for sink in composed_event_sinks):
        resolved_event_sink = CompositeRuntimeEventSink(
            (event_sink, *composed_event_sinks)
        )

    driver_factory = create_paper_runtime_driver_factory(
        session_id=session_id,
        initial_cash=initial_cash,
        dependencies=runtime_dependencies,
        event_sink=resolved_event_sink,
        checkpoint_sink=checkpoint_sink,
        runtime_result_sink=runtime_result_sink,
        interval_seconds=interval_seconds,
        environment=environment,
        active_model=active_model,
    )

    return DesktopRuntimeBootstrap(
        scanner_infrastructure=scanner_infrastructure,
        runtime_dependencies=runtime_dependencies,
        driver_factory=driver_factory,
        order_projection=order_projection,
        position_projection=position_projection,
        timeline_projection=timeline_projection,
        decision_projection=decision_projection,
        portfolio_projection=portfolio_projection,
    )


__all__ = [
    "DesktopRuntimeBootstrap",
    "create_desktop_runtime_bootstrap",
]
