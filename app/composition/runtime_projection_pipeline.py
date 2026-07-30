"""Shared composition for event-driven runtime projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.operations.runtime import RuntimeEventSink
from app.operations_core import OperationsBus
from app.read_models.decision_projection import DecisionProjection
from app.read_models.health_projection import HealthProjection
from app.read_models.order_projection import OrderProjection
from app.read_models.portfolio_projection import PortfolioProjection
from app.read_models.position_projection import PositionProjection
from app.read_models.timeline_projection import TimelineProjection
from app.read_models.watchlist_projection import WatchlistProjection

from .runtime_event_sink import CompositeRuntimeEventSink


@dataclass(frozen=True, slots=True)
class RuntimeProjectionPipeline:
    order_projection: OrderProjection
    position_projection: PositionProjection
    portfolio_projection: PortfolioProjection
    health_projection: HealthProjection
    watchlist_projection: WatchlistProjection
    timeline_projection: TimelineProjection
    decision_projection: DecisionProjection
    sink: CompositeRuntimeEventSink

    @property
    def sinks(self) -> tuple[RuntimeEventSink, ...]:
        return self.sink.sinks


def create_runtime_projection_pipeline(
    *,
    operations_bus: OperationsBus,
    account_id: str,
    timeline_history_limit: int = 500,
    watchlist_maximum_symbols: int = 100,
    watchlist_stale_after: timedelta = timedelta(seconds=30),
) -> RuntimeProjectionPipeline:
    """Build the authoritative ordered projection fan-out."""

    if not isinstance(operations_bus, OperationsBus):
        raise TypeError("operations_bus must be an OperationsBus")
    order_projection = OrderProjection(operations_bus)
    position_projection = PositionProjection(
        operations_bus,
        account_id=account_id,
    )
    portfolio_projection = PortfolioProjection(
        operations_bus,
        position_projection=position_projection,
        order_projection=order_projection,
    )
    health_projection = HealthProjection(operations_bus)
    watchlist_projection = WatchlistProjection(
        operations_bus,
        maximum_symbols=watchlist_maximum_symbols,
        stale_after=watchlist_stale_after,
    )
    timeline_projection = TimelineProjection(
        operations_bus,
        maximum_entries=timeline_history_limit,
    )
    decision_projection = DecisionProjection(operations_bus)
    sink = CompositeRuntimeEventSink(
        (
            order_projection,
            position_projection,
            portfolio_projection,
            health_projection,
            watchlist_projection,
            timeline_projection,
            decision_projection,
        )
    )
    return RuntimeProjectionPipeline(
        order_projection=order_projection,
        position_projection=position_projection,
        portfolio_projection=portfolio_projection,
        health_projection=health_projection,
        watchlist_projection=watchlist_projection,
        timeline_projection=timeline_projection,
        decision_projection=decision_projection,
        sink=sink,
    )


__all__ = [
    "RuntimeProjectionPipeline",
    "create_runtime_projection_pipeline",
]
