import pytest
from app.execution_planner import *
from tests.execution_planner.fixtures import request
def test_serializers():
 value=request();plan=DeterministicExecutionPlannerEvaluator().evaluate(value,ExecutionPlannerPolicy(enabled=True));assert serialize_request(value)==value.to_dict() and serialize_plan(plan)==plan.to_dict() and serialize_policy(ExecutionPlannerPolicy())==ExecutionPlannerPolicy().to_dict()
def test_wrong_type():
 with pytest.raises(ExecutionPlannerSerializationError):serialize_request(object())
