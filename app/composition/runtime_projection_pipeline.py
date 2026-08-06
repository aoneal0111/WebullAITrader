"""Shared composition for event-driven runtime projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from app.operations.runtime import RuntimeEventSink
from app.operations_core import OperationsBus, PortfolioIntelligenceUpdated, PortfolioObservationPublished
from app.read_models.decision_projection import DecisionProjection
from app.read_models.health_projection import HealthProjection
from app.read_models.order_projection import OrderProjection
from app.read_models.portfolio_projection import PortfolioProjection
from app.read_models.position_projection import PositionProjection
from app.read_models.timeline_projection import TimelineProjection
from app.read_models.watchlist_projection import WatchlistProjection
from app.portfolio_intelligence.projection import PortfolioIntelligenceProjection
from app.portfolio_intelligence.models import PortfolioAccount
from app.portfolio_intelligence.events import portfolio_observation_event_id
from app.portfolio_intelligence.runtime import PortfolioIntelligenceService

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
    portfolio_intelligence_projection: PortfolioIntelligenceProjection
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
    portfolio_account_source: Callable[[], PortfolioAccount] | None = None,
    portfolio_intelligence_service: PortfolioIntelligenceService | None = None,
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
    portfolio_intelligence_projection = PortfolioIntelligenceProjection(
        account_id=account_id,
        position_projection=position_projection,
        order_projection=order_projection,
        account_source=portfolio_account_source,
        service=portfolio_intelligence_service,
        observation_sink=lambda observation: operations_bus.publish(
            PortfolioObservationPublished(
                occurred_at=observation.occurred_at,
                event_id=portfolio_observation_event_id(observation),
                source="portfolio-intelligence-observation",
                observation=observation,
            )
        ),
        snapshot_sink=lambda snapshot: operations_bus.publish(
            PortfolioIntelligenceUpdated(
                occurred_at=snapshot.generated_at,
                source="portfolio-intelligence-projection",
                snapshot=snapshot,
            )
        ),
    )
    sink = CompositeRuntimeEventSink(
        (
            order_projection,
            position_projection,
            portfolio_projection,
            health_projection,
            watchlist_projection,
            timeline_projection,
            decision_projection,
            portfolio_intelligence_projection,
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
        portfolio_intelligence_projection=portfolio_intelligence_projection,
        sink=sink,
    )


__all__ = [
    "RuntimeProjectionPipeline",
    "create_runtime_projection_pipeline",
]
