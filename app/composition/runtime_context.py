"""Composition helpers for the runtime coordination context pipeline."""

from __future__ import annotations

from app.execution_coordinator.runtime_context_input_source import (
    ConfiguredRuntimeContextInputSource,
    GFVDecisionSource,
    MarketQuoteSource,
    MarketStateSource,
    RuntimeContextConfiguration,
    TimestampSource,
)
from app.execution_coordinator.runtime_context_provider import (
    RuntimeCoordinationContextProvider,
)


def create_runtime_context_provider(
    *,
    configuration: RuntimeContextConfiguration,
    timestamp_source: TimestampSource,
    market_state_source: MarketStateSource,
    market_quote_source: MarketQuoteSource,
    gfv_decision_source: GFVDecisionSource,
) -> RuntimeCoordinationContextProvider:
    """Compose the production runtime coordination-context provider."""

    input_source = ConfiguredRuntimeContextInputSource(
        configuration=configuration,
        timestamp_source=timestamp_source,
        market_state_source=market_state_source,
        market_quote_source=market_quote_source,
        gfv_decision_source=gfv_decision_source,
    )

    return RuntimeCoordinationContextProvider(
        input_source=input_source,
    )


__all__ = ["create_runtime_context_provider"]
