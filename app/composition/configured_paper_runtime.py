"""Composition root for configured paper-runtime dependencies."""

from __future__ import annotations

from collections.abc import Callable

from app.execution_coordinator.paper_request_builder import PaperRequestBuilder
from app.execution_coordinator.runtime_context_input_source import (
    GFVDecisionSource,
    MarketQuoteSource,
    MarketStateSource,
    RuntimeContextConfiguration,
    TimestampSource,
)
from app.operations.learning_runtime import RuntimeInferenceAdapter
from app.operations.runtime import Clock
from app.operations.scanner_runtime import (
    ScannerCoordinator,
    ScannerRuntimeCycle,
    SnapshotResolver,
)
from app.operations_core import OperationsBus, ScannerSnapshotUpdated
from app.strategy_engine import StrategyEngine
from app.strategy_engine.order_intent_factory import (
    QuantityProvider,
    RequestIdProvider,
    RuntimeOrderIntentFactory,
)

from .execution_pipeline import create_paper_execution_pipeline
from .paper_dependencies import (
    PaperRuntimeDependencies,
    create_live_snapshot_source,
    create_paper_runtime_dependencies,
)
from .runtime_context import create_runtime_context_provider


def create_configured_paper_runtime_dependencies(
    *,
    scanner_coordinator: ScannerCoordinator,
    snapshot_resolver: SnapshotResolver,
    quantity_provider: QuantityProvider,
    request_id_provider: RequestIdProvider,
    runtime_context_configuration: RuntimeContextConfiguration,
    timestamp_source: TimestampSource,
    market_state_source: MarketStateSource,
    market_quote_source: MarketQuoteSource,
    gfv_decision_source: GFVDecisionSource,
    clock: Clock,
    strategy_engine: StrategyEngine | None = None,
    inference_adapter: RuntimeInferenceAdapter | None = None,
    candidate_limit: int = 25,
    maximum_events_per_cycle: int = 1000,
    scanner_cycle_sink: Callable[[ScannerRuntimeCycle], None] | None = None,
    operations_bus: OperationsBus | None = None,
) -> PaperRuntimeDependencies:
    """
    Assemble paper-runtime dependencies from application-supplied authorities.

    This composition root owns dependency wiring only. It does not invent
    trading quantities, market state, account policy, scanner lifecycle, or
    execution configuration.
    """

    cycle_sink = _compose_scanner_cycle_sink(
        operations_bus=operations_bus,
        scanner_cycle_sink=scanner_cycle_sink,
    )

    snapshot_source = create_live_snapshot_source(
        coordinator=scanner_coordinator,
        snapshot_resolver=snapshot_resolver,
        candidate_limit=candidate_limit,
        maximum_events_per_cycle=maximum_events_per_cycle,
        cycle_sink=cycle_sink,
    )

    context_provider = create_runtime_context_provider(
        configuration=runtime_context_configuration,
        timestamp_source=timestamp_source,
        market_state_source=market_state_source,
        market_quote_source=market_quote_source,
        gfv_decision_source=gfv_decision_source,
    )

    order_intent_factory = RuntimeOrderIntentFactory(
        quantity_provider=quantity_provider,
        request_id_provider=request_id_provider,
    )

    request_builder = PaperRequestBuilder(
        order_intent_factory=order_intent_factory,
        context_provider=context_provider,
    )

    return create_paper_runtime_dependencies(
        snapshot_source=snapshot_source,
        coordinator=create_paper_execution_pipeline(),
        request_builder=request_builder,
        clock=clock,
        strategy_engine=strategy_engine,
        inference_adapter=inference_adapter,
    )


def _compose_scanner_cycle_sink(
    *,
    operations_bus: OperationsBus | None,
    scanner_cycle_sink: Callable[[ScannerRuntimeCycle], None] | None,
) -> Callable[[ScannerRuntimeCycle], None] | None:
    if operations_bus is None:
        return scanner_cycle_sink

    def publish_cycle(cycle: ScannerRuntimeCycle) -> None:
        operations_bus.publish(
            ScannerSnapshotUpdated(
                occurred_at=cycle.timestamp,
                source="scanner-runtime",
                ranked_symbols=cycle.ranked_symbols,
                resolved_symbols=cycle.resolved_symbols,
                missing_symbols=cycle.missing_symbols,
                events_read=cycle.events_read,
                decisions_created=cycle.decisions_created,
            )
        )

        if scanner_cycle_sink is not None:
            scanner_cycle_sink(cycle)

    return publish_cycle


__all__ = [
    "create_configured_paper_runtime_dependencies",
]
