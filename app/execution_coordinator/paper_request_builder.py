from __future__ import annotations

from dataclasses import dataclass

from app.execution_coordinator.context_provider import (
    CoordinationContextProvider,
)
from app.execution_coordinator.contexts import CoordinationRequest
from app.strategy_engine.order_intent_factory import (
    RuntimeOrderIntentFactory,
)
from app.strategy_engine.models import StrategyDecision


@dataclass(frozen=True, slots=True)
class PaperRequestBuilder:
    """
    Runtime composition object responsible for assembling immutable
    CoordinationRequest instances.

    Trading policy is supplied by injected collaborators rather than
    implemented here.
    """

    order_intent_factory: RuntimeOrderIntentFactory
    context_provider: CoordinationContextProvider

    def __call__(
        self,
        decision: StrategyDecision,
        snapshot,
        session,
        cycle: int,
        symbol_index: int,
    ) -> CoordinationRequest:
        context = self.context_provider.get_context(decision.symbol)

        return CoordinationRequest(
            order_intent=self.order_intent_factory.create(decision),
            advisory_response=decision,
            snapshot=snapshot,
            risk_limits=context.risk_limits,
            account_state=context.account_state,
            market_state=context.market_state,
            gfv_decision=context.gfv_decision,
            compliance_limits=context.compliance_limits,
            kill_switch=context.kill_switch,
            portfolio=context.portfolio,
            market_quote=context.market_quote,
            execution_config=context.execution_config,
            journal=context.journal,
            equity_curve=context.equity_curve,
        )
