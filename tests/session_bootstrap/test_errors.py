import pytest
from app.session_bootstrap import *
from tests.session_bootstrap.fixtures import approved_profile,request
from tests.session_bootstrap.helpers import FakeAuthenticationRuntime,FakeProvider,FakeSessionManager
def test_credential_failure_normalized_with_cause():
 raw=LookupError("synthetic");runtime=DeterministicSessionBootstrapRuntime(approved_profile(),FakeProvider(error=raw),FakeAuthenticationRuntime(),FakeSessionManager(),SessionBootstrapPolicy(enabled=True))
 with pytest.raises(SessionBootstrapCredentialError) as captured:runtime.bootstrap(request())
 assert captured.value.__cause__ is raw
def test_invalid_dependency_outputs_raise_not_failure_result():
 runtime=DeterministicSessionBootstrapRuntime(approved_profile(),FakeProvider(result=object()),FakeAuthenticationRuntime(),FakeSessionManager(),SessionBootstrapPolicy(enabled=True))
 with pytest.raises(SessionBootstrapDependencyError):runtime.bootstrap(request())
