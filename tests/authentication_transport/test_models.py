from dataclasses import FrozenInstanceError
import pytest

from app.authentication_transport import (
    AuthenticationTransportContext, AuthenticationTransportRequest,
    AuthenticationTransportResult, AuthenticationVerificationResult,
)
from tests.authentication_transport.helpers import connector_request


def test_context_request_and_verification_are_frozen_and_round_trip():
    context = AuthenticationTransportContext("correlation-1", {"caller": "outer"})
    request = AuthenticationTransportRequest(
        "attempt-1", connector_request().authentication_request, context)
    verification = AuthenticationVerificationResult(False, "DENIED")
    assert AuthenticationTransportContext.from_dict(context.to_dict()) == context
    assert AuthenticationTransportRequest.from_dict(request.to_dict()) == request
    assert AuthenticationVerificationResult.from_dict(verification.to_dict()) == verification
    assert not hasattr(request, "__dict__")
    with pytest.raises(FrozenInstanceError):
        request.attempt_id = "changed"
    with pytest.raises(TypeError):
        context.metadata["x"] = True


def test_failed_result_round_trips_and_preserves_caller_values():
    request = connector_request()
    result = AuthenticationTransportResult(
        request.attempt_id, False, AuthenticationVerificationResult(False, "DENIED"),
        None, "response-1", request.context, "authentication_transport_policy_v1")
    assert AuthenticationTransportResult.from_dict(result.to_dict()) == result
    assert result.attempt_id == "attempt-1"
    assert result.context is request.context


def test_result_requires_consistent_success_and_lifecycle_result():
    request = connector_request()
    with pytest.raises(ValueError):
        AuthenticationTransportResult(
            request.attempt_id, True, AuthenticationVerificationResult(False, "DENIED"),
            None, "response-1", request.context, "policy")
