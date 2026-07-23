import pytest
from app.order_placement import *
from app.order_placement.validation import validate_dependencies,validate_request
from tests.order_placement.helpers import FakeGateway,FakeSessionManager
@pytest.mark.parametrize("args",[(None,FakeGateway(),OrderPlacementPolicy()),(FakeSessionManager(),None,OrderPlacementPolicy()),(FakeSessionManager(),FakeGateway(),object())])
def test_dependencies(args):
 with pytest.raises(OrderPlacementDependencyError):validate_dependencies(*args)
def test_request_type():
 with pytest.raises(OrderPlacementValidationError):validate_request(object())
