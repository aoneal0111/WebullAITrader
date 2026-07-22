from datetime import datetime, timezone
from decimal import Decimal

from app.execution_planner import (ExecutionInstruction, ExecutionPlan, ExecutionPlanCriteriaResult,
                                   ExecutionPlanDecision, ExecutionPlanResult)
from app.order_placement import OrderSide, OrderType, TimeInForce
from app.paper_trading import PaperExecutionRequest, PaperTradingAccount

NOW = datetime(2026, 1, 2, 15, 30, tzinfo=timezone.utc)


def account(cash="10000", positions=()):
    market = sum((x.market_value for x in positions), Decimal("0"))
    unrealized = sum((x.unrealized_profit_loss for x in positions), Decimal("0"))
    realized = sum((x.realized_profit_loss for x in positions), Decimal("0"))
    return PaperTradingAccount("acct", Decimal(cash), Decimal(cash), tuple(positions), (), (), realized,
                               unrealized, market, Decimal(cash) + market)


def plan(side=OrderSide.BUY, quantity="10", symbol="AAPL", order_type=OrderType.MARKET,
         limit_price=None, stop_price=None, account_id="acct", decision=ExecutionPlanDecision.PLANNED):
    criteria = (ExecutionPlanCriteriaResult("valid", True, "valid result"),)
    if decision is not ExecutionPlanDecision.PLANNED:
        return ExecutionPlanResult("plan-1", decision, None, criteria, "v1")
    instruction = ExecutionInstruction(account_id, symbol, side, Decimal(quantity), order_type, TimeInForce.DAY,
                                       Decimal(limit_price) if limit_price else None,
                                       Decimal(stop_price) if stop_price else None)
    return ExecutionPlanResult("plan-1", decision, ExecutionPlan("plan-1", (instruction,)), criteria, "v1")


def request(state=None, **plan_kwargs):
    return PaperExecutionRequest("req-1", "acct", plan(**plan_kwargs), state or account(), Decimal("100"), NOW, {"source": "test"})


class Evaluator:
    def __init__(self, quantity=None, error=None): self.quantity, self.error, self.calls = quantity, error, []
    def evaluate(self, request, instruction, account, market_price, policy):
        self.calls.append((request, instruction, account, market_price, policy))
        if self.error: raise self.error
        return instruction.quantity if self.quantity is None else Decimal(self.quantity)
