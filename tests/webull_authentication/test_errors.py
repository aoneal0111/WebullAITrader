import pytest
from app.webull_authentication import WebullAuthenticationRequestError,WebullAuthenticationRequestFactory,WebullAuthenticationResponseVerifier
from tests.webull_authentication.fixtures import policy,profile
from tests.webull_authentication.helpers import auth_request,response
def test_missing_identifiers_do_not_leak_metadata_values():
 request=type(auth_request())("broker","sign-in",("username_ref","password_ref"),{"unrelated":"sensitive-fixture-value"})
 with pytest.raises(WebullAuthenticationRequestError) as captured:WebullAuthenticationRequestFactory(profile(),policy()).create(request)
 assert "sensitive-fixture-value" not in str(captured.value);assert captured.value.__cause__ is not None
def test_verifier_type_errors_do_not_leak_raw_values():
 with pytest.raises(Exception) as captured:WebullAuthenticationResponseVerifier(profile(),policy()).verify(auth_request(),object())
 assert "actual-password-value" not in str(captured.value)
