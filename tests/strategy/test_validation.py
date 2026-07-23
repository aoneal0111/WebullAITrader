import pytest
from app.strategy import *
from app.strategy.validation import validate_context,validate_dependencies
from tests.strategy.helpers import FakeEvaluator
@pytest.mark.parametrize("args",[(None,StrategyPolicy()),(FakeEvaluator(),object())])
def test_dependency_validation(args):
 with pytest.raises(StrategyDependencyError):validate_dependencies(*args)
def test_context_validation():
 with pytest.raises(StrategyValidationError):validate_context(object())
