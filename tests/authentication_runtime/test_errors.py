import pytest
from app.authentication_runtime import *
from tests.authentication_runtime.helpers import FakeConnector,FakeProvider,request
from tests.authentication_runtime.test_runtime import runtime
def test_credential_failure_invalid_output_and_connector_failure_normalized():
 raw=LookupError("fixture")
 with pytest.raises(AuthenticationRuntimeCredentialError) as e:runtime(provider=FakeProvider(error=raw)).authenticate(request())
 assert e.value.__cause__ is raw
 with pytest.raises(AuthenticationRuntimeCredentialError):runtime(provider=FakeProvider(result=object())).authenticate(request())
 with pytest.raises(AuthenticationRuntimeExecutionError) as e:runtime(connector=FakeConnector(error=raw)).authenticate(request())
 assert e.value.__cause__ is raw
def test_validation_failure_normalized():
 with pytest.raises(AuthenticationRuntimeValidationError):runtime().authenticate(object())
