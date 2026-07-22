import pytest
from app.open_orders import *
from app.open_orders.validation import validate_dependencies,validate_request
from tests.open_orders.helpers import FakeGateway,FakeSessionManager
@pytest.mark.parametrize("args",[(None,FakeGateway(),OpenOrdersPolicy()),(FakeSessionManager(),None,OpenOrdersPolicy()),(FakeSessionManager(),FakeGateway(),object())])
def test_dependencies(args):
 with pytest.raises(OpenOrdersDependencyError):validate_dependencies(*args)
def test_request_type():
 with pytest.raises(OpenOrdersValidationError):validate_request(object())
