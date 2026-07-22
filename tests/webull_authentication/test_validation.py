import pytest
from app.webull_authentication import *
from tests.webull_authentication.fixtures import profile,policy
from tests.webull_authentication.helpers import auth_request,response
def test_dependencies_models_and_field_path():
 assert validate_dependencies(profile(),policy());assert validate_request(auth_request());assert validate_response(response({"a":{"b":1}}));assert field_path({"a":{"b":1}},("a","b"))==1
def test_invalid_dependencies_and_paths_normalized():
 with pytest.raises(WebullAuthenticationDependencyError):validate_dependencies(object(),policy())
 with pytest.raises(WebullAuthenticationDependencyError):validate_dependencies(profile(),object())
 with pytest.raises(WebullAuthenticationVerificationError):field_path({"a":{}},("a","b"))
