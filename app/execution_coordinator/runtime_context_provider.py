from __future__ import annotations

from dataclasses import dataclass, field

from app.execution_coordinator.context_provider import (
    CoordinationContext,
    CoordinationContextProvider,
)
from app.execution_coordinator.runtime_context_assembler import (
    RuntimeContextAssembler,
)
from app.paper_session import PaperTradingSession
from app.strategy_engine import StrategyOrderIntent


@dataclass(frozen=True, slots=True)
class RuntimeCoordinationContextProvider(CoordinationContextProvider):
    """Production coordination-context provider for the paper runtime."""

    assembler: RuntimeContextAssembler = field(
        default_factory=RuntimeContextAssembler
    )

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
        raise NotImplementedError(
            "Runtime context inputs have not been composed yet."
        )
