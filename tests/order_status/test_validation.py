import pytest
from app.order_status import *
from app.order_status.validation import validate_dependencies,validate_request
from tests.order_status.helpers import FakeGateway,FakeSessionManager
@pytest.mark.parametrize("args",[(None,FakeGateway(),OrderStatusPolicy()),(FakeSessionManager(),None,OrderStatusPolicy()),(FakeSessionManager(),FakeGateway(),object())])
def test_dependencies(args):
 with pytest.raises(OrderStatusDependencyError):validate_dependencies(*args)
def test_request_type():
 with pytest.raises(OrderStatusValidationError):validate_request(object())
