import pytest
from app.market_data import *
from app.market_data.validation import validate_dependencies,validate_request
from tests.market_data.helpers import FakeGateway,FakeSessionManager
@pytest.mark.parametrize("args",[(None,FakeGateway(),MarketDataPolicy()),(FakeSessionManager(),None,MarketDataPolicy()),(FakeSessionManager(),FakeGateway(),object())])
def test_dependencies(args):
 with pytest.raises(MarketDataDependencyError):validate_dependencies(*args)
def test_request_type():
 with pytest.raises(MarketDataValidationError):validate_request(object())
