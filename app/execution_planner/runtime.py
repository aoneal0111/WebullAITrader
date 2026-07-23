from app.execution_planner.exceptions import ExecutionPlannerDependencyError,ExecutionPlannerEvaluationError
from app.execution_planner.models import *
from app.execution_planner.validation import validate_dependencies,validate_request
from app.order_placement import OrderSide,OrderType,TimeInForce
from app.risk import RiskOutcome
from app.strategy import StrategySignal
class DeterministicExecutionPlannerEvaluator:
 def evaluate(self,request,policy):
  context=request.risk_context;decision=context.strategy_decision;configuration=context.strategy_context.configuration
  order_type=OrderType(configuration.get("order_type","LIMIT"));time_in_force=TimeInForce(configuration.get("time_in_force","DAY"));side=OrderSide.BUY if decision.signal is StrategySignal.BUY else OrderSide.SELL
  limit_price=context.reference_price if order_type in (OrderType.LIMIT,OrderType.STOP_LIMIT) else None
  stop_raw=configuration.get("stop_price");stop_price=stop_raw if order_type in (OrderType.STOP,OrderType.STOP_LIMIT) else None
  instruction=ExecutionInstruction(context.strategy_context.portfolio.account_id,decision.symbol,side,request.risk_result.approved_quantity,order_type,time_in_force,limit_price,stop_price,{"deterministic":True})
  return ExecutionPlan(request.request_id,(instruction,),{"deterministic":True})
class DeterministicExecutionPlannerRuntime:
 def __init__(self,evaluator,policy):validate_dependencies(evaluator,policy);self._evaluator=evaluator;self._policy=policy
 def plan(self,request):
  request=validate_request(request)
  if not self._policy.enabled:return self._result(request,ExecutionPlanDecision.DISABLED,None,(False,False,False))
  context,result=request.risk_context,request.risk_result
  identities=result.context_id==context.context_id and result.strategy_decision==context.strategy_decision and result.strategy_decision.symbol==context.strategy_decision.symbol
  account=context.strategy_context.portfolio.account_id
  position_accounts=all(p.symbol!=context.strategy_decision.symbol or p.symbol==result.strategy_decision.symbol for p in context.strategy_context.portfolio.positions)
  if not identities or not account or not position_accounts or result.approved_quantity>context.requested_quantity:return self._result(request,ExecutionPlanDecision.INVALID_RISK_RESULT,None,(True,False,False))
  if result.outcome is RiskOutcome.REJECTED or context.strategy_decision.signal is StrategySignal.HOLD:return self._result(request,ExecutionPlanDecision.REJECTED,None,(True,True,False))
  if result.outcome not in (RiskOutcome.APPROVED,RiskOutcome.MODIFIED) or result.approved_quantity<=0:return self._result(request,ExecutionPlanDecision.INVALID_RISK_RESULT,None,(True,False,False))
  try:plan=self._evaluator.evaluate(request,self._policy)
  except Exception as exc:raise ExecutionPlannerEvaluationError("execution planner evaluator failed") from exc
  if not isinstance(plan,ExecutionPlan) or plan.request_id!=request.request_id:raise ExecutionPlannerDependencyError("planner evaluator returned invalid plan")
  instruction=plan.instructions[0]
  if instruction.account_id!=account or instruction.symbol!=context.strategy_decision.symbol or instruction.quantity!=result.approved_quantity:raise ExecutionPlannerDependencyError("planned instruction identity or quantity mismatch")
  return self._result(request,ExecutionPlanDecision.PLANNED,plan,(True,True,True))
 def _result(self,request,decision,plan,passed):
  names=("policy_enabled","risk_eligible","instruction_valid");details=("execution planner policy enabled","risk outcome eligible for planning","one broker-neutral instruction validated")
  return ExecutionPlanResult(request.request_id,decision,plan,tuple(ExecutionPlanCriteriaResult(n,p,d) for n,p,d in zip(names,passed,details)),self._policy.version,{"deterministic":True})
