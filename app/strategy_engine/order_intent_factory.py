from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from app.strategy_engine.intents import (
    StrategyOrderIntent,
    create_order_intent,
)
from app.strategy_engine.models import StrategyDecision


RequestIdProvider = Callable[[], str]
QuantityProvider = Callable[[StrategyDecision], Decimal]


@dataclass(frozen=True, slots=True)
class RuntimeOrderIntentFactory:
    """
    Runtime composition object responsible for constructing executable
    StrategyOrderIntent instances.

    Trading policy (position sizing) and request ID generation are injected
    dependencies rather than embedded business logic.
    """

    quantity_provider: QuantityProvider
    request_id_provider: RequestIdProvider

    def create(
        self,
        decision: StrategyDecision,
    ) -> StrategyOrderIntent:
        return create_order_intent(
            decision,
            quantity=self.quantity_provider(decision),
            request_id=self.request_id_provider(),
        )
