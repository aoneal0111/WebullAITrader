import pytest
from app.authentication_runtime import *
from tests.authentication_runtime.helpers import FakeConnector,FakeProvider,request,transport_result
def runtime(provider=None,connector=None,policy=None):return DeterministicAuthenticationRuntime(provider or FakeProvider(),connector or FakeConnector(),policy or AuthenticationRuntimePolicy(enabled=True))
def test_disabled_default_calls_nothing():
 p,c=FakeProvider(),FakeConnector();subject=runtime(p,c,AuthenticationRuntimePolicy())
 with pytest.raises(AuthenticationRuntimeDisabledError):subject.authenticate(request())
 assert p.calls==c.calls==[]
def test_success_looks_up_once_invokes_once_preserves_and_is_deterministic():
 p,c=FakeProvider(),FakeConnector();subject=runtime(p,c);source=request();first=subject.authenticate(source);second=subject.authenticate(request());assert first==second;assert len(p.calls)==len(c.calls)==2;assert first.attempt_id==source.attempt_id;assert first.context is source.context
 built=c.calls[0];assert built.attempt_id=="attempt-1";assert built.authentication_request.metadata["correlation_id"]=="correlation-1";assert source.to_dict()==request().to_dict()
def test_failed_connector_result_preserved():
 result=runtime(connector=FakeConnector(transport_result(False))).authenticate(request());assert not result.success
def test_each_single_execution_calls_each_dependency_once():
 p,c=FakeProvider(),FakeConnector();runtime(p,c).authenticate(request());assert len(p.calls)==len(c.calls)==1
