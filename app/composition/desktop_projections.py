from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.paper_trading.command_composition import PAPER_ACCOUNT_ID
from app.portfolio_intelligence import (
    PortfolioIntelligenceService,
    PortfolioRiskLimits,
    load_portfolio_intelligence_configuration,
)

from .desktop_trading_state import DesktopTradingStateSources
from .runtime_projection_pipeline import create_runtime_projection_pipeline


@dataclass(slots=True)
class DesktopProjectionComposition:
    runtime_projections: object
    trading_state_sources: DesktopTradingStateSources
    paper_campaign_holder: dict[str, object]


def create_desktop_projection_composition(
    *,
    bus: object,
    state_store: object,
    operational_configuration: object,
) -> DesktopProjectionComposition:
    """Compose runtime projections and their read-only trading state adapters."""

    paper_campaign_holder: dict[str, object] = {
        "id": None,
        "capital": None,
    }
    trading_state_holder: dict[str, DesktopTradingStateSources] = {}

    runtime_projections = create_runtime_projection_pipeline(
        operations_bus=bus,
        account_id=(
            operational_configuration.account_id
            or PAPER_ACCOUNT_ID
        ),
        watchlist_stale_after=timedelta(
            seconds=(
                operational_configuration.maximum_market_data_age_seconds
            )
        ),
        portfolio_account_source=lambda: (
            trading_state_holder["sources"].portfolio_account()
        ),
        portfolio_intelligence_service=PortfolioIntelligenceService(
            configuration=load_portfolio_intelligence_configuration(),
            limits=PortfolioRiskLimits(
                maximum_open_positions=(
                    operational_configuration.max_open_positions
                ),
            ),
        ),
        paper_account_campaign_id_source=lambda: paper_campaign_holder["id"],
        paper_account_capital_source=lambda: (
            paper_campaign_holder["capital"]
        ),
    )

    trading_state_sources = DesktopTradingStateSources(
        state_store=state_store,
        runtime_projections=runtime_projections,
        operational_configuration=operational_configuration,
    )
    trading_state_holder["sources"] = trading_state_sources

    return DesktopProjectionComposition(
        runtime_projections=runtime_projections,
        trading_state_sources=trading_state_sources,
        paper_campaign_holder=paper_campaign_holder,
    )


__all__ = [
    "DesktopProjectionComposition",
    "create_desktop_projection_composition",
]
