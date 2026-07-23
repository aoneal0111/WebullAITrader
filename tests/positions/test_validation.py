import pytest
from app.positions import *
from app.positions.validation import validate_dependencies,validate_request
from tests.positions.helpers import FakeGateway,FakeSessionManager
@pytest.mark.parametrize("args",[(None,FakeGateway(),PositionsPolicy()),(FakeSessionManager(),None,PositionsPolicy()),(FakeSessionManager(),FakeGateway(),object())])
def test_dependencies(args):
 with pytest.raises(PositionsDependencyError):validate_dependencies(*args)
def test_request_type():
 with pytest.raises(PositionsValidationError):validate_request(object())
