from app.strategy.exceptions import StrategyDependencyError,StrategyEvaluationError
from app.strategy.models import StrategyDecision,StrategyResult
from app.strategy.validation import validate_context,validate_dependencies
class DeterministicStrategyRuntime:
 def __init__(self,evaluator,policy):validate_dependencies(evaluator,policy);self._evaluator=evaluator;self._policy=policy
 def evaluate(self,context):
  context=validate_context(context)
  if not self._policy.enabled:return StrategyResult(context.context_id,False,(),self._policy.version,{"deterministic":True,"reason":"DISABLED"})
  try:decisions=self._evaluator.evaluate(context)
  except Exception as exc:raise StrategyEvaluationError("strategy evaluator failed") from exc
  if not isinstance(decisions,tuple) or any(not isinstance(x,StrategyDecision) for x in decisions):raise StrategyDependencyError("strategy evaluator returned invalid decisions")
  return StrategyResult(context.context_id,True,decisions,self._policy.version,{"deterministic":True})
