from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest

from app.execution_orchestrator import (ExecutionOrchestratorDependencyError, ExecutionOrchestratorPolicy,
                                        ExecutionOrchestratorRuntime, ExecutionOrchestratorStageError,
                                        PaperTradingCycleOutcome)
from app.execution_planner import ExecutionPlanDecision, ExecutionPlanResult
from app.paper_trading import PaperExecutionOutcome
from app.risk import RiskCriteriaResult, RiskOutcome, RiskResult
from app.strategy import StrategyDecision, StrategyResult, StrategySignal
from tests.execution_orchestrator.helpers import RuntimeSpy, paper_account, real_engine, request


def unused(): return RuntimeSpy("evaluate", lambda value: None), RuntimeSpy("evaluate", lambda value: None), RuntimeSpy("plan", lambda value: None), RuntimeSpy("execute", lambda value: None)


def test_disabled_calls_nothing_and_preserves_account():
    strategy, risk, planner, paper = unused(); original = request().paper_account
    result = ExecutionOrchestratorRuntime(strategy, risk, planner, paper, ExecutionOrchestratorPolicy()).execute(request())
    assert result.outcome is PaperTradingCycleOutcome.DISABLED and result.resulting_account == original
    assert not strategy.calls and not risk.calls and not planner.calls and not paper.calls


@pytest.mark.parametrize("signal,with_position,outcome", [(StrategySignal.BUY, False, PaperTradingCycleOutcome.EXECUTED),
                                                           (StrategySignal.SELL, True, PaperTradingCycleOutcome.EXECUTED)])
def test_complete_buy_and_sell_cycles(signal, with_position, outcome):
    engine, evaluator = real_engine(signal); result = engine.execute(request(with_position))
    assert result.outcome is outcome and len(evaluator.calls) == 1
    assert result.strategy_result and result.risk_result and result.execution_plan_result and result.paper_execution_result


def test_full_execution_replaces_account():
    req = request(); result = real_engine()[0].execute(req)
    assert result.outcome is PaperTradingCycleOutcome.EXECUTED and result.resulting_account is result.paper_execution_result.account
    assert result.resulting_account != req.paper_account


def test_partial_execution():
    result = real_engine(partial=True)[0].execute(request())
    assert result.outcome is PaperTradingCycleOutcome.PARTIALLY_EXECUTED
    assert result.paper_execution_result.outcome is PaperExecutionOutcome.PARTIALLY_EXECUTED


@pytest.mark.parametrize("signal,with_position", [(StrategySignal.HOLD, False), (StrategySignal.EXIT, False)])
def test_strategy_no_action_short_circuit(signal, with_position):
    decision = StrategyDecision("AAPL", signal, "1", ("test",))
    strategy = RuntimeSpy("evaluate", lambda context: StrategyResult(context.context_id, True, (decision,), "v1"))
    risk, planner, paper = RuntimeSpy("evaluate", lambda x: None), RuntimeSpy("plan", lambda x: None), RuntimeSpy("execute", lambda x: None)
    req = request(with_position); result = ExecutionOrchestratorRuntime(strategy, risk, planner, paper, ExecutionOrchestratorPolicy(enabled=True)).execute(req)
    assert result.outcome is PaperTradingCycleOutcome.NO_ACTION and result.resulting_account is req.paper_account
    assert len(strategy.calls) == 1 and not risk.calls and not planner.calls and not paper.calls


def setup_through_risk(outcome=RiskOutcome.REJECTED, approved="0"):
    decision = StrategyDecision("AAPL", StrategySignal.BUY, "1", ("test",))
    strategy = RuntimeSpy("evaluate", lambda context: StrategyResult(context.context_id, True, (decision,), "v1"))
    def risk_response(context):
        return RiskResult(context.context_id, decision, outcome, context.requested_quantity, approved,
                          (RiskCriteriaResult("risk", outcome is not RiskOutcome.REJECTED, "0", None, "risk check"),), "v1")
    return strategy, RuntimeSpy("evaluate", risk_response)


def test_risk_rejection_short_circuit():
    strategy, risk = setup_through_risk(); planner, paper = RuntimeSpy("plan", lambda x: None), RuntimeSpy("execute", lambda x: None)
    req = request(); result = ExecutionOrchestratorRuntime(strategy, risk, planner, paper, ExecutionOrchestratorPolicy(enabled=True)).execute(req)
    assert result.outcome is PaperTradingCycleOutcome.RISK_REJECTED and result.resulting_account is req.paper_account
    assert len(strategy.calls) == len(risk.calls) == 1 and not planner.calls and not paper.calls


def test_risk_modified_quantity_propagates_to_planner_and_paper():
    policy = __import__("app.risk", fromlist=["RiskPolicy"]).RiskPolicy(enabled=True, max_order_quantity="4")
    result = real_engine(risk_policy=policy)[0].execute(request())
    assert result.risk_result.outcome is RiskOutcome.MODIFIED and result.risk_result.approved_quantity == 4
    instruction = result.execution_plan_result.plan.instructions[0]
    assert instruction.quantity == 4 and result.paper_execution_result.order.requested_quantity == 4


def test_planner_rejection_short_circuit():
    strategy, risk = setup_through_risk(RiskOutcome.APPROVED, "10")
    def reject(value):
        from app.execution_planner import ExecutionPlanCriteriaResult
        return ExecutionPlanResult(value.request_id, ExecutionPlanDecision.REJECTED, None,
                                   (ExecutionPlanCriteriaResult("plan", False, "rejected"),), "v1")
    planner, paper = RuntimeSpy("plan", reject), RuntimeSpy("execute", lambda x: None)
    req = request(); result = ExecutionOrchestratorRuntime(strategy, risk, planner, paper, ExecutionOrchestratorPolicy(enabled=True)).execute(req)
    assert result.outcome is PaperTradingCycleOutcome.PLANNING_REJECTED and result.resulting_account is req.paper_account
    assert len(planner.calls) == 1 and not paper.calls


@pytest.mark.parametrize("stage,method", [("strategy", "evaluate"), ("risk", "evaluate"), ("planning", "plan"), ("paper_execution", "execute")])
def test_stage_exception_normalization_and_cause(stage, method):
    engine, _ = real_engine(); failing = RuntimeSpy(method, error=KeyError("raw"))
    setattr(engine, {"strategy":"_strategy", "risk":"_risk", "planning":"_planner", "paper_execution":"_paper"}[stage], failing)
    with pytest.raises(ExecutionOrchestratorStageError) as caught: engine.execute(request())
    assert caught.value.stage == stage and isinstance(caught.value.__cause__, KeyError) and len(failing.calls) == 1


def test_strategy_result_request_identity_mismatch():
    decision = StrategyDecision("AAPL", StrategySignal.BUY, "1", ("test",))
    strategy = RuntimeSpy("evaluate", lambda context: StrategyResult("wrong", True, (decision,), "v1"))
    risk, planner, paper = unused()[1:]
    with pytest.raises(ExecutionOrchestratorDependencyError):
        ExecutionOrchestratorRuntime(strategy, risk, planner, paper, ExecutionOrchestratorPolicy(enabled=True)).execute(request())
    assert not risk.calls and not planner.calls and not paper.calls


def test_deterministic_repeated_runs_and_immutable_result():
    engine, _ = real_engine(); req = request(); first, second = engine.execute(req), engine.execute(req)
    assert first == second and first.to_dict() == second.to_dict()
    with pytest.raises(FrozenInstanceError): first.outcome = PaperTradingCycleOutcome.FAILED
