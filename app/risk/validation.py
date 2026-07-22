from app.risk.exceptions import RiskRuntimeDependencyError,RiskRuntimeValidationError
from app.risk.models import RiskContext
from app.risk.policies import RiskPolicy
def validate_runtime_dependencies(evaluator,policy):
 if evaluator is None or not callable(getattr(evaluator,"evaluate",None)):raise RiskRuntimeDependencyError("risk evaluator must expose evaluate(context, policy)")
 if not isinstance(policy,RiskPolicy):raise RiskRuntimeDependencyError("policy must be RiskPolicy")
def validate_context(context):
 if not isinstance(context,RiskContext):raise RiskRuntimeValidationError("context must be RiskContext")
 return context
