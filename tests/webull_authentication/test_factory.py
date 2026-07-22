import pytest
from app.http_runtime import HTTPMethod
from app.webull_authentication import WebullAuthenticationDisabledError,WebullAuthenticationPolicy,WebullAuthenticationRequestError,WebullAuthenticationRequestFactory
from tests.webull_authentication.fixtures import policy,profile
from tests.webull_authentication.helpers import auth_request
def test_factory_maps_only_profile_fields_references_and_identifiers():
 source=auth_request();result=WebullAuthenticationRequestFactory(profile(),policy()).create(source)
 assert result.request_id=="attempt-1:webull-auth-request";assert result.method is HTTPMethod.POST;assert result.url=="https://mock.invalid/authenticate";assert result.headers==profile().static_headers
 assert result.body=={"synthetic_user":{"credential_reference":"username_ref"},"synthetic_secret":{"credential_reference":"password_ref"}}
 assert result.context.correlation_id=="correlation-1";assert source.metadata["attempt_id"]=="attempt-1";assert result==WebullAuthenticationRequestFactory(profile(),policy()).create(auth_request())
def test_optional_device_reference():
 result=WebullAuthenticationRequestFactory(profile(),policy(include_device_identifier=True)).create(auth_request());assert result.body["synthetic_device"]=={"credential_reference":"device_ref"}
def test_disabled_and_missing_reference_are_normalized():
 with pytest.raises(WebullAuthenticationDisabledError):WebullAuthenticationRequestFactory(profile(),WebullAuthenticationPolicy()).create(auth_request())
 bad=auth_request();bad=type(bad)(bad.broker_identifier,bad.credential_purpose,("username_ref",),bad.metadata)
 with pytest.raises(WebullAuthenticationRequestError):WebullAuthenticationRequestFactory(profile(),policy()).create(bad)
def test_no_sensitive_value_in_repr_or_serialization():
 result=WebullAuthenticationRequestFactory(profile(),policy()).create(auth_request());rendered=repr(result.to_dict());assert "actual-password-value" not in rendered;assert "credential_reference" in rendered
