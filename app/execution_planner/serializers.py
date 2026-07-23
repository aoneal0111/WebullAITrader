from app.execution_planner.exceptions import ExecutionPlannerSerializationError
from app.execution_planner.models import ExecutionInstruction,ExecutionPlan,ExecutionPlanCriteriaResult,ExecutionPlanRequest,ExecutionPlanResult
from app.execution_planner.policies import ExecutionPlannerPolicy
def _serialize(value,expected):
 if not isinstance(value,expected):raise ExecutionPlannerSerializationError(f"value must be {expected.__name__}")
 return value.to_dict()
def serialize_request(value):return _serialize(value,ExecutionPlanRequest)
def serialize_instruction(value):return _serialize(value,ExecutionInstruction)
def serialize_plan(value):return _serialize(value,ExecutionPlan)
def serialize_criteria(value):return _serialize(value,ExecutionPlanCriteriaResult)
def serialize_result(value):return _serialize(value,ExecutionPlanResult)
def serialize_policy(value):return _serialize(value,ExecutionPlannerPolicy)
