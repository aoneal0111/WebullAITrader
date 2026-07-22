import httpx
import pytest

from app.authentication_transport import (
    AuthenticationLifecycleError, AuthenticationRequestCreationError,
    AuthenticationRequestExecutionError, AuthenticationResponseVerificationError,
)
from tests.authentication_transport.helpers import (
    FakeAuthenticationService, FakePipeline, FakeRequestFactory, FakeResponseVerifier,
    FakeTransport, connector_request,
)
from tests.authentication_transport.test_connector import build


@pytest.mark.parametrize("factory,pipeline,transport,verifier,expected", [
    (FakeRequestFactory(error=LookupError()), None, None, None, AuthenticationRequestCreationError),
    (None, FakePipeline(prepare_error=LookupError()), None, None, AuthenticationRequestCreationError),
    (None, None, FakeTransport(error=httpx.ReadTimeout("timeout")), None, AuthenticationRequestExecutionError),
    (None, None, FakeTransport(error=httpx.ConnectError("connection")), None, AuthenticationRequestExecutionError),
    (None, FakePipeline(finalize_error=LookupError()), None, None, AuthenticationRequestExecutionError),
    (None, None, None, FakeResponseVerifier(error=LookupError()), AuthenticationResponseVerificationError),
])
def test_stage_failures_are_normalized_without_retry(factory, pipeline, transport, verifier, expected):
    service = FakeAuthenticationService()
    connector, values = build(service=service, factory=factory, pipeline=pipeline,
                              transport=transport, verifier=verifier)
    with pytest.raises(expected) as captured:
        connector.authenticate(connector_request())
    assert captured.value.__cause__ is not None
    assert service.calls == []
    assert all(len(getattr(value, "calls", [])) <= 1 for value in values)


def test_lifecycle_failure_is_normalized_after_verified_transport():
    service = FakeAuthenticationService(error=LookupError())
    connector, _ = build(service=service)
    with pytest.raises(AuthenticationLifecycleError) as captured:
        connector.authenticate(connector_request())
    assert isinstance(captured.value.__cause__, LookupError)
    assert len(service.calls) == 1
