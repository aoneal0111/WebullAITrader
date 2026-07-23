import pytest
from app.execution_planner import *
from app.execution_planner.validation import validate_dependencies,validate_request
from tests.execution_planner.helpers import FakeEvaluator
@pytest.mark.parametrize("args",[(None,ExecutionPlannerPolicy()),(FakeEvaluator(),object())])
def test_dependencies(args):
 with pytest.raises(ExecutionPlannerDependencyError):validate_dependencies(*args)
def test_request_type():
 with pytest.raises(ExecutionPlannerValidationError):validate_request(object())
