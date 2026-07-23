from datetime import datetime, timezone
from decimal import Decimal

from app.execution_planner import (DeterministicExecutionPlannerEvaluator, DeterministicExecutionPlannerRuntime,
                                   ExecutionPlannerPolicy)
from app.paper_trading import (CompletePaperFillEvaluator, PaperPosition, PaperTradingAccount,
                               PaperTradingPolicy, PaperTradingRuntime)
from app.portfolio import PortfolioPosition, PortfolioSnapshot
from app.risk import DeterministicRiskEvaluator, DeterministicRiskRuntime, RiskPolicy
from app.strategy import StrategyDecision, StrategyPolicy, StrategyResult, StrategySignal
from app.strategy.runtime import DeterministicStrategyRuntime
from app.execution_orchestrator import ExecutionOrchestratorPolicy, ExecutionOrchestratorRuntime, PaperTradingCycleRequest

NOW = datetime(2026, 2, 3, 14, 0, tzinfo=timezone.utc)


class StrategyEvaluator:
    def __init__(self, signal=StrategySignal.BUY, symbol="AAPL", error=None): self.signal, self.symbol, self.error, self.calls = signal, symbol, error, []
    def evaluate(self, context):
        self.calls.append(context)
        if self.error: raise self.error
        return (StrategyDecision(self.symbol, self.signal, Decimal("1"), ("deterministic",)),)


class PartialEvaluator:
    def evaluate(self, request, instruction, account, market_price, policy): return instruction.quantity / 2


class RuntimeSpy:
    def __init__(self, method, callback=None, error=None): self.method, self.callback, self.error, self.calls = method, callback, error, []
    def __getattr__(self, name):
        if name != self.method: raise AttributeError(name)
        def call(value):
            self.calls.append(value)
            if self.error: raise self.error
            return self.callback(value)
        return call


def portfolio(with_position=False):
    positions = (PortfolioPosition("AAPL", "10", "1000", "800", "200", "1"),) if with_position else ()
    market = sum((x.market_value for x in positions), Decimal("0"))
    return PortfolioSnapshot("acct", "10000", "10000", Decimal("10000") + market, market,
                             Decimal("10000") + market, positions)


def paper_account(with_position=False):
    positions = (PaperPosition("acct", "AAPL", "10", "80", "100", "1000", "200"),) if with_position else ()
    return PaperTradingAccount("acct", "10000", "10000", positions, (), (), "0", "200" if with_position else "0",
                               "1000" if with_position else "0", "11000" if with_position else "10000")


def request(with_position=False, quantity="10", **metadata):
    return PaperTradingCycleRequest("cycle-1", "acct", portfolio(with_position), paper_account(with_position), "100", NOW,
                                    quantity, {"strategy_configuration": {"order_type": "MARKET"}, **metadata})


def real_engine(signal=StrategySignal.BUY, partial=False, risk_policy=None):
    strategy_evaluator = StrategyEvaluator(signal)
    strategy = DeterministicStrategyRuntime(strategy_evaluator, StrategyPolicy(enabled=True))
    risk = DeterministicRiskRuntime(DeterministicRiskEvaluator(), risk_policy or RiskPolicy(enabled=True))
    planner = DeterministicExecutionPlannerRuntime(DeterministicExecutionPlannerEvaluator(), ExecutionPlannerPolicy(enabled=True))
    fill_evaluator = PartialEvaluator() if partial else CompletePaperFillEvaluator()
    paper = PaperTradingRuntime(fill_evaluator, PaperTradingPolicy(enabled=True, allow_partial_fills=partial))
    engine = ExecutionOrchestratorRuntime(strategy, risk, planner, paper, ExecutionOrchestratorPolicy(enabled=True))
    return engine, strategy_evaluator
