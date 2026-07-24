from __future__ import annotations

from collections.abc import Callable
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
class RuntimeContextInputs:
    """Authoritative non-session inputs required for runtime coordination."""

    account_type: object
    timestamp: object
    market_state: object
    risk_limits: object
    compliance_limits: object
    gfv_decision: object
    kill_switch: object
    market_quote: object
    execution_config: object


RuntimeContextInputSource = Callable[..., RuntimeContextInputs]


@dataclass(frozen=True, slots=True)
class RuntimeCoordinationContextProvider(CoordinationContextProvider):
    """Production coordination-context provider for the paper runtime."""

    input_source: RuntimeContextInputSource | None = None
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
        if self.input_source is None:
            raise NotImplementedError(
                "Runtime context inputs have not been composed yet."
            )

        inputs = self.input_source(
            order_intent=order_intent,
            symbol=symbol,
            snapshot=snapshot,
            session=session,
            cycle=cycle,
            symbol_index=symbol_index,
        )

        if not isinstance(inputs, RuntimeContextInputs):
            raise TypeError(
                "runtime context input source must return RuntimeContextInputs"
            )

        return self.assembler.build(
            portfolio=session.portfolio,
            account_type=inputs.account_type,
            filled_orders=session.statistics.orders_filled,
            symbol=symbol,
            timestamp=inputs.timestamp,
            market_state=inputs.market_state,
            risk_limits=inputs.risk_limits,
            compliance_limits=inputs.compliance_limits,
            gfv_decision=inputs.gfv_decision,
            kill_switch=inputs.kill_switch,
            market_quote=inputs.market_quote,
            execution_config=inputs.execution_config,
            journal=session.journal,
            equity_curve=session.equity_curve,
        )


__all__ = [
    "RuntimeContextInputs",
    "RuntimeContextInputSource",
    "RuntimeCoordinationContextProvider",
]
