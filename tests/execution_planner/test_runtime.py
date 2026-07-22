import pytest
from app.execution_planner import *
from app.order_placement import OrderSide
from app.risk import RiskOutcome,RiskResult
from app.strategy import StrategyDecision,StrategySignal
from tests.execution_planner.fixtures import enabled_policy,request
from tests.execution_planner.helpers import FakeEvaluator
def runtime(evaluator=None,policy=None):return DeterministicExecutionPlannerRuntime(evaluator or FakeEvaluator(),policy or enabled_policy())
@pytest.mark.parametrize("signal,side",[(StrategySignal.BUY,OrderSide.BUY),(StrategySignal.SELL,OrderSide.SELL),(StrategySignal.EXIT,OrderSide.SELL)])
def test_directional_mapping_exactly_once(signal,side):
 e=FakeEvaluator();result=runtime(e).plan(request(signal));assert result.decision is ExecutionPlanDecision.PLANNED and result.plan.instructions[0].side is side and len(e.calls)==1
def test_modified_quantity_propagates_exactly():
 result=runtime().plan(request(outcome=RiskOutcome.MODIFIED,approved="1"));assert result.plan.instructions[0].quantity==1
def test_rejected_risk_zero_calls():
 e=FakeEvaluator();result=runtime(e).plan(request(outcome=RiskOutcome.REJECTED,approved="0"));assert result.decision is ExecutionPlanDecision.REJECTED and not e.calls
def test_hold_is_deterministic_no_action_zero_calls():
 e=FakeEvaluator();result=runtime(e).plan(request(StrategySignal.HOLD,requested="0",approved="0"));assert result.decision is ExecutionPlanDecision.REJECTED and result.plan is None and not e.calls
def test_disabled_zero_calls():
 e=FakeEvaluator();result=runtime(e,ExecutionPlannerPolicy()).plan(request());assert result.decision is ExecutionPlanDecision.DISABLED and not e.calls
def test_mismatched_risk_symbol_invalid_zero_calls():
 value=request();other=StrategyDecision("MSFT",StrategySignal.BUY,"0.8",("synthetic",));risk=RiskResult(value.risk_result.context_id,other,RiskOutcome.APPROVED,"2","2",value.risk_result.criteria_results,"v");bad=ExecutionPlanRequest(value.request_id,value.risk_context,risk)
 e=FakeEvaluator();assert runtime(e).plan(bad).decision is ExecutionPlanDecision.INVALID_RISK_RESULT and not e.calls
def test_evaluator_account_symbol_and_quantity_mismatch_rejected():
 value=request();plan=DeterministicExecutionPlannerEvaluator().evaluate(value,enabled_policy());instruction=plan.instructions[0]
 for changed in (ExecutionInstruction("other",instruction.symbol,instruction.side,instruction.quantity,instruction.order_type,instruction.time_in_force,instruction.limit_price),ExecutionInstruction(instruction.account_id,"MSFT",instruction.side,instruction.quantity,instruction.order_type,instruction.time_in_force,instruction.limit_price),ExecutionInstruction(instruction.account_id,instruction.symbol,instruction.side,"1",instruction.order_type,instruction.time_in_force,instruction.limit_price)):
  with pytest.raises(ExecutionPlannerDependencyError):runtime(FakeEvaluator(ExecutionPlan(value.request_id,(changed,)))).plan(value)
def test_evaluator_failure_normalized_no_retry():
 e=FakeEvaluator(error=KeyError("raw"))
 with pytest.raises(ExecutionPlannerEvaluationError) as caught:runtime(e).plan(request())
 assert isinstance(caught.value.__cause__,KeyError) and len(e.calls)==1
def test_determinism_and_input_immutability():
 value=request();before=value.to_dict();assert runtime().plan(value)==runtime().plan(value) and value.to_dict()==before
