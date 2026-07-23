import pytest
from app.session_bootstrap import *
from tests.session_bootstrap.fixtures import approved_profile,request
from tests.session_bootstrap.helpers import FakeAuthenticationRuntime,FakeProvider,FakeSessionManager
def test_valid_dependencies_and_request():assert validate_dependencies(approved_profile(),FakeProvider(),FakeAuthenticationRuntime(),FakeSessionManager(),SessionBootstrapPolicy());assert validate_request(request())
@pytest.mark.parametrize("index",range(5))
def test_invalid_dependencies(index):
 values=[approved_profile(),FakeProvider(),FakeAuthenticationRuntime(),FakeSessionManager(),SessionBootstrapPolicy()];values[index]=object()
 with pytest.raises(SessionBootstrapDependencyError):validate_dependencies(*values)
def test_invalid_request():
 with pytest.raises(SessionBootstrapValidationError):validate_request(object())
