from app.execution_planner.exceptions import ExecutionPlannerDependencyError,ExecutionPlannerValidationError
from app.execution_planner.models import ExecutionPlanRequest
from app.execution_planner.policies import ExecutionPlannerPolicy
def validate_dependencies(evaluator,policy):
 if evaluator is None or not callable(getattr(evaluator,"evaluate",None)):raise ExecutionPlannerDependencyError("planner evaluator must expose evaluate(request, policy)")
 if not isinstance(policy,ExecutionPlannerPolicy):raise ExecutionPlannerDependencyError("policy must be ExecutionPlannerPolicy")
def validate_request(request):
 if not isinstance(request,ExecutionPlanRequest):raise ExecutionPlannerValidationError("request must be ExecutionPlanRequest")
 return request
