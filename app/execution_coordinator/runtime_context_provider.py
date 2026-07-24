from __future__ import annotations

from dataclasses import dataclass

from app.execution_coordinator.context_provider import (
    CoordinationContext,
    CoordinationContextProvider,
)
from app.paper_session import PaperTradingSession
from app.strategy_engine import StrategyOrderIntent


@dataclass(frozen=True, slots=True)
class RuntimeCoordinationContextProvider(CoordinationContextProvider):
    """Production coordination-context provider for the paper runtime."""

    def get_context(
        self,
        *,
        order_intent: StrategyOrderIntent,
        symbol: str,
        snapshot: object,
        session: PaperTradingSession,
        cycle: int,
        symbol_index: int,
    ) -> CoordinationContext:
        raise NotImplementedError
