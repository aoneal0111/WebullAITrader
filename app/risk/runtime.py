from decimal import Decimal
from app.risk.exceptions import RiskRuntimeDependencyError,RiskRuntimeEvaluationError
from app.risk.models import RiskContext,RiskCriteriaResult,RiskOutcome,RiskResult
from app.risk.validation import validate_context,validate_runtime_dependencies
class DeterministicRiskEvaluator:
 def evaluate(self,context,policy):
  portfolio=context.strategy_context.portfolio;decision=context.strategy_decision;requested=context.requested_quantity;price=context.reference_price
  position=next((p for p in portfolio.positions if p.symbol==decision.symbol),None)
  current_position=abs(position.market_value) if position else Decimal("0");gross=sum((abs(p.market_value) for p in portfolio.positions),Decimal("0"))
  capacities=[policy.max_order_quantity]
  criteria=[]
  if decision.signal.value=="HOLD":approved=Decimal("0");outcome=RiskOutcome.APPROVED
  elif decision.signal.value in ("SELL","EXIT"):
   available=abs(position.quantity) if position else Decimal("0");capacities.append(available);approved=min([requested,*capacities]);outcome=RiskOutcome.REJECTED if approved<=0 else RiskOutcome.MODIFIED if approved<requested else RiskOutcome.APPROVED
  else:
   capacities.extend((max(Decimal("0"),(policy.max_position_value-current_position)/price),max(Decimal("0"),(policy.max_portfolio_exposure-gross)/price),max(Decimal("0"),(portfolio.cash-policy.minimum_cash_reserve)/price)))
   approved=min([requested,*capacities]);outcome=RiskOutcome.REJECTED if approved<=0 else RiskOutcome.MODIFIED if approved<requested else RiskOutcome.APPROVED
  names=("order_quantity","position_value","portfolio_exposure","cash_reserve");limits=(policy.max_order_quantity,policy.max_position_value,policy.max_portfolio_exposure,policy.minimum_cash_reserve)
  observed=(requested,current_position+requested*price,gross+requested*price,portfolio.cash-requested*price)
  passed=(requested<=policy.max_order_quantity,decision.signal.value!="BUY" or observed[1]<=policy.max_position_value,decision.signal.value!="BUY" or observed[2]<=policy.max_portfolio_exposure,decision.signal.value!="BUY" or observed[3]>=policy.minimum_cash_reserve)
  if outcome is RiskOutcome.MODIFIED and not policy.allow_modification:approved=Decimal("0");outcome=RiskOutcome.REJECTED
  criteria=tuple(RiskCriteriaResult(n,p,o,l,f"{n} policy check") for n,p,o,l in zip(names,passed,observed,limits))
  return RiskResult(context.context_id,decision,outcome,requested,approved,criteria,policy.version,{"deterministic":True})
class DeterministicRiskRuntime:
 def __init__(self,evaluator,policy):validate_runtime_dependencies(evaluator,policy);self._evaluator=evaluator;self._policy=policy
 def evaluate(self,context):
  context=validate_context(context)
  if not self._policy.enabled:return RiskResult(context.context_id,context.strategy_decision,RiskOutcome.REJECTED,context.requested_quantity,Decimal("0"),(RiskCriteriaResult("policy_enabled",False,Decimal("0"),None,"risk runtime disabled"),),self._policy.version,{"deterministic":True})
  try:result=self._evaluator.evaluate(context,self._policy)
  except Exception as exc:raise RiskRuntimeEvaluationError("risk evaluator failed") from exc
  if not isinstance(result,RiskResult) or result.context_id!=context.context_id or result.strategy_decision!=context.strategy_decision:raise RiskRuntimeDependencyError("risk evaluator returned invalid result")
  return result
