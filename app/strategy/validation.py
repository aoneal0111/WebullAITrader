from app.strategy.exceptions import StrategyDependencyError,StrategyValidationError
from app.strategy.models import StrategyContext
from app.strategy.policies import StrategyPolicy
def validate_dependencies(evaluator,policy):
 if evaluator is None or not callable(getattr(evaluator,"evaluate",None)):raise StrategyDependencyError("strategy evaluator must expose evaluate(context)")
 if not isinstance(policy,StrategyPolicy):raise StrategyDependencyError("policy must be StrategyPolicy")
def validate_context(context):
 if not isinstance(context,StrategyContext):raise StrategyValidationError("context must be StrategyContext")
 return context
