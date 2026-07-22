from typing import Protocol
from app.execution_planner.models import ExecutionPlan,ExecutionPlanRequest,ExecutionPlanResult
from app.execution_planner.policies import ExecutionPlannerPolicy
class ExecutionPlannerEvaluator(Protocol):
 def evaluate(self,request:ExecutionPlanRequest,policy:ExecutionPlannerPolicy)->ExecutionPlan:...
class ExecutionPlannerRuntime(Protocol):
 def plan(self,request:ExecutionPlanRequest)->ExecutionPlanResult:...
