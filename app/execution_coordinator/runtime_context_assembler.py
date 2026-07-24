from dataclasses import dataclass
from typing import Any, Callable

from app.execution_coordinator.context_provider import CoordinationContext
from app.order_compliance.account_state_builder import build_account_state


@dataclass(frozen=True)
class RuntimeContextAssembler:
    """Assembles a coordination context from authoritative runtime inputs."""

    account_state_builder: Callable[..., Any] = build_account_state

    def build(
        self,
        *,
        portfolio: object,
        account_type: object,
        filled_orders: int,
        symbol: str,
        timestamp: object,
        market_state: object,
        risk_limits: object,
        compliance_limits: object,
        gfv_decision: object,
        kill_switch: object,
        market_quote: object,
        execution_config: object,
        journal: object,
        equity_curve: object,
    ) -> CoordinationContext:
        account_state = self.account_state_builder(
            portfolio=portfolio,
            account_type=account_type,
            filled_orders=filled_orders,
            symbol=symbol,
            timestamp=timestamp,
        )

        return CoordinationContext(
            account_state=account_state,
            market_state=market_state,
            risk_limits=risk_limits,
            compliance_limits=compliance_limits,
            gfv_decision=gfv_decision,
            kill_switch=kill_switch,
            portfolio=portfolio,
            market_quote=market_quote,
            execution_config=execution_config,
            journal=journal,
            equity_curve=equity_curve,
        )
