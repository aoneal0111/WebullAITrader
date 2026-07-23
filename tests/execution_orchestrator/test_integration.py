from dataclasses import replace

from app.execution_orchestrator import PaperTradingCycleOutcome
from app.paper_trading import PaperTradingAccount
from app.strategy import StrategyResult
from tests.execution_orchestrator.helpers import RuntimeSpy, real_engine, request


def test_real_runtime_chain_invokes_each_stage_and_executes_buy():
    engine, strategy_evaluator = real_engine(); result = engine.execute(request())
    assert len(strategy_evaluator.calls) == 1
    assert result.outcome is PaperTradingCycleOutcome.EXECUTED
    assert result.risk_result.approved_quantity == result.execution_plan_result.plan.instructions[0].quantity
    assert result.paper_execution_result.order.requested_quantity == result.risk_result.approved_quantity


def test_strategy_empty_result_short_circuits_all_downstream_stages():
    strategy = RuntimeSpy("evaluate", lambda context: StrategyResult(context.context_id, True, (), "v1"))
    risk = RuntimeSpy("evaluate", lambda value: None); planner = RuntimeSpy("plan", lambda value: None); paper = RuntimeSpy("execute", lambda value: None)
    from app.execution_orchestrator import ExecutionOrchestratorPolicy, ExecutionOrchestratorRuntime
    result = ExecutionOrchestratorRuntime(strategy, risk, planner, paper, ExecutionOrchestratorPolicy(enabled=True)).execute(request())
    assert result.outcome is PaperTradingCycleOutcome.STRATEGY_REJECTED
    assert len(strategy.calls) == 1 and not risk.calls and not planner.calls and not paper.calls


def test_paper_execution_rejection_preserves_original_account():
    req = request(); insufficient = PaperTradingAccount("acct", "1", "1", (), (), (), "0", "0", "0", "1")
    req = replace(req, paper_account=insufficient)
    result = real_engine()[0].execute(req)
    assert result.outcome is PaperTradingCycleOutcome.EXECUTION_REJECTED
    assert result.resulting_account is result.paper_execution_result.account
    assert result.resulting_account == insufficient
