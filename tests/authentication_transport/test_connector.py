import pytest

from app.authentication import AuthenticationStatus
from app.authentication_transport import (
    AuthenticationTransportDisabledError, AuthenticationTransportPolicy,
    AuthenticationVerificationResult, DeterministicAuthenticationTransportConnector,
)
from tests.authentication_transport.helpers import (
    FakeAuthenticationService, FakePipeline, FakeRequestFactory, FakeResponseVerifier,
    FakeTransport, connector_request,
)


def build(policy=None, service=None, factory=None, pipeline=None, transport=None, verifier=None):
    values = (service or FakeAuthenticationService(), factory or FakeRequestFactory(),
              pipeline or FakePipeline(), transport or FakeTransport(),
              verifier or FakeResponseVerifier())
    return DeterministicAuthenticationTransportConnector(
        *values, policy or AuthenticationTransportPolicy(enabled=True)), values


def test_disabled_default_has_no_collaborator_calls():
    connector, values = build(policy=AuthenticationTransportPolicy())
    with pytest.raises(AuthenticationTransportDisabledError):
        connector.authenticate(connector_request())
    assert all(not getattr(value, "calls", []) for value in values)


def test_success_calls_every_stage_exactly_once_and_preserves_values():
    connector, (service, factory, pipeline, transport, verifier) = build()
    request = connector_request()
    result = connector.authenticate(request)
    assert result.success and result.attempt_id == request.attempt_id
    assert result.context is request.context
    assert result.response_identifier == "http-request-1:response"
    assert len(factory.calls) == len(pipeline.prepared) == len(transport.calls) == 1
    assert len(pipeline.finalized) == len(verifier.calls) == len(service.calls) == 1
    assert service.state().status is AuthenticationStatus.AUTHENTICATED


def test_failed_verification_returns_result_and_never_begins_authentication():
    verifier = FakeResponseVerifier(AuthenticationVerificationResult(False, "DENIED"))
    service = FakeAuthenticationService()
    connector, _ = build(service=service, verifier=verifier)
    first = connector.authenticate(connector_request())
    second = connector.authenticate(connector_request())
    assert first == second and not first.success
    assert service.calls == []
    assert service.state().status is AuthenticationStatus.UNAUTHENTICATED
