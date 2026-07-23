import pytest
from app.risk import *
from app.risk.validation import validate_context,validate_runtime_dependencies
from tests.risk.helpers import FakeEvaluator
@pytest.mark.parametrize("args",[(None,RiskPolicy()),(FakeEvaluator(),object())])
def test_runtime_dependencies(args):
 with pytest.raises(RiskRuntimeDependencyError):validate_runtime_dependencies(*args)
def test_runtime_context_type():
 with pytest.raises(RiskRuntimeValidationError):validate_context(object())
