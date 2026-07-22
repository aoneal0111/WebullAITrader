import pytest
from app.order_cancellation import *
from app.order_cancellation.validation import validate_dependencies,validate_request
from tests.order_cancellation.helpers import FakeGateway,FakeSessionManager
@pytest.mark.parametrize("args",[(None,FakeGateway(),OrderCancellationPolicy()),(FakeSessionManager(),None,OrderCancellationPolicy()),(FakeSessionManager(),FakeGateway(),object())])
def test_dependencies(args):
 with pytest.raises(OrderCancellationDependencyError):validate_dependencies(*args)
def test_request_type():
 with pytest.raises(OrderCancellationValidationError):validate_request(object())
