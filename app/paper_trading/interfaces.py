from decimal import Decimal
from typing import Protocol

from app.execution_planner import ExecutionInstruction
from app.paper_trading.milestone_models import PaperExecutionRequest, PaperTradingAccount
from app.paper_trading.policies import PaperTradingPolicy


class PaperFillEvaluator(Protocol):
    def evaluate(self, request: PaperExecutionRequest, instruction: ExecutionInstruction,
                 account: PaperTradingAccount, market_price: Decimal,
                 policy: PaperTradingPolicy) -> Decimal: ...
