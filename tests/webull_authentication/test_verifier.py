import pytest
from app.webull_authentication import WebullAuthenticationResponseError,WebullAuthenticationResponseVerifier,WebullAuthenticationVerificationError
from tests.webull_authentication.fixtures import MALFORMED,REJECTED,SUCCESS,policy,profile
from tests.webull_authentication.helpers import auth_request,response
def test_success_nested_extraction_ignores_secret_like_fields():
 result=WebullAuthenticationResponseVerifier(profile(),policy()).verify(auth_request(),response(SUCCESS));assert result.success;assert result.reason=="VERIFIED";assert result.metadata["outputs"]=={"decision_code":"fixture-ok"};assert "must-not-persist" not in repr(result.to_dict())
def test_explicit_rejection_and_unexpected_value():
 result=WebullAuthenticationResponseVerifier(profile(),policy()).verify(auth_request(),response(REJECTED));assert not result.success;assert result.reason=="UNEXPECTED_SUCCESS_VALUE"
def test_missing_path_malformed_and_required_header():
 verifier=WebullAuthenticationResponseVerifier(profile(),policy())
 with pytest.raises(WebullAuthenticationVerificationError):verifier.verify(auth_request(),response(MALFORMED))
 with pytest.raises(WebullAuthenticationResponseError):verifier.verify(auth_request(),response(SUCCESS,headers=()))
def test_invalid_status_is_deterministic_rejection():
 result=WebullAuthenticationResponseVerifier(profile(),policy()).verify(auth_request(),response(SUCCESS,status=401));assert not result.success;assert result.reason=="HTTP_STATUS_REJECTED"
def test_non_mapping_body_rejected_without_body_leak():
 with pytest.raises(WebullAuthenticationResponseError) as error:WebullAuthenticationResponseVerifier(profile(),policy()).verify(auth_request(),response("secret-body"))
 assert "secret-body" not in str(error.value)
