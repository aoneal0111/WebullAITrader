import pytest
from app.authentication_runtime import *
from tests.authentication_runtime.helpers import FakeConnector,FakeProvider,request
def test_valid_dependencies_and_request():assert validate_dependencies(FakeProvider(),FakeConnector(),AuthenticationRuntimePolicy());assert validate_request(request())
@pytest.mark.parametrize("values",[(object(),FakeConnector(),AuthenticationRuntimePolicy()),(FakeProvider(),object(),AuthenticationRuntimePolicy()),(FakeProvider(),FakeConnector(),object())])
def test_invalid_dependencies(values):
 with pytest.raises(AuthenticationRuntimeDependencyError):validate_dependencies(*values)
def test_invalid_request():
 with pytest.raises(AuthenticationRuntimeValidationError):validate_request(object())
