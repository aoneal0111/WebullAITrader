import pytest
from app.portfolio import *
from app.portfolio.validation import validate_dependencies,validate_request
from tests.portfolio.helpers import FakeAccountRuntime,FakePositionsRuntime
@pytest.mark.parametrize("args",[(None,FakePositionsRuntime(),PortfolioPolicy()),(FakeAccountRuntime(),None,PortfolioPolicy()),(FakeAccountRuntime(),FakePositionsRuntime(),object())])
def test_dependencies(args):
 with pytest.raises(PortfolioDependencyError):validate_dependencies(*args)
def test_request_type():
 with pytest.raises(PortfolioValidationError):validate_request(object())
