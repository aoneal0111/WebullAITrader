from __future__ import annotations

from dataclasses import dataclass

from app.operations.runtime import PaperRuntimeEvent, RuntimeHealthUpdate
from app.services.chart_market_data import ChartMarketDataService
from app.strategies.warrior_momentum.execution_quote import (
    WebullExecutionQuoteSource,
)
from app.webull.client_factories import MarketDataClientFactory
from app.webull.market_data_session import utc_now
from app.webull.request_audit import (
    AuditedMarketDataClient,
    RequestIsolationGuard,
)
from app.webull.sdk_market_data import LazyOfficialDataClient


@dataclass(slots=True)
class DesktopMarketServices:
    """Shared REST market-data services with unchanged trading semantics."""

    shared_rest_market_data: LazyOfficialDataClient
    chart_market_data_service: ChartMarketDataService
    execution_quote_source: WebullExecutionQuoteSource
    chart_default_symbol: str | None = None


def create_desktop_market_services(
    *,
    chart_market_configuration: object,
    chart_request_guard: RequestIsolationGuard,
    runtime_projections: object,
) -> DesktopMarketServices:
    """Construct chart and execution-quote clients around one audited REST owner."""

    chart_observation_sequence = 0

    def publish_chart_observation(
        event_type: str,
        symbol: str,
        count: int,
    ) -> None:
        nonlocal chart_observation_sequence
        chart_observation_sequence += 1
        runtime_projections.sink(
            PaperRuntimeEvent(
                sequence=chart_observation_sequence,
                timestamp=utc_now(),
                event_type=event_type,
                message=(
                    f"Loaded {count} historical bars for {symbol} through REST."
                ),
                cycle=0,
                symbol=symbol,
                source="atlas-chart-rest",
                health=RuntimeHealthUpdate(
                    market_data_status="CONNECTED",
                    market_data_rest_status="CONNECTED",
                    historical_bars_status="AVAILABLE",
                ),
            )
        )

    shared_rest_market_data = LazyOfficialDataClient(
        lambda: AuditedMarketDataClient(
            MarketDataClientFactory(chart_market_configuration).create(),
            chart_request_guard,
            chart_market_configuration,
        )
    )
    chart_market_data_service = ChartMarketDataService(
        shared_rest_market_data,
        observation_sink=publish_chart_observation,
    )
    execution_quote_source = WebullExecutionQuoteSource(
        shared_rest_market_data
    )

    return DesktopMarketServices(
        shared_rest_market_data=shared_rest_market_data,
        chart_market_data_service=chart_market_data_service,
        execution_quote_source=execution_quote_source,
        chart_default_symbol=None,
    )


__all__ = [
    "DesktopMarketServices",
    "create_desktop_market_services",
]
