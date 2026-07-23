from app.authentication import AuthenticationResult
from app.authentication_transport.exceptions import (
    AuthenticationLifecycleError, AuthenticationRequestCreationError,
    AuthenticationRequestExecutionError, AuthenticationResponseVerificationError,
    AuthenticationTransportDisabledError,
)
from app.authentication_transport.models import (
    AuthenticationTransportResult, AuthenticationVerificationResult,
)
from app.authentication_transport.policies import AuthenticationTransportPolicy
from app.authentication_transport.validation import validate_dependencies, validate_request
from app.http_pipeline import HTTPRequestOperation, HTTPResponseOperation


class DeterministicAuthenticationTransportConnector:
    def __init__(self, authentication_service, request_factory, pipeline, transport,
                 response_verifier, policy: AuthenticationTransportPolicy):
        validate_dependencies(authentication_service, request_factory, pipeline,
                              transport, response_verifier, policy)
        self._authentication_service = authentication_service
        self._request_factory = request_factory
        self._pipeline = pipeline
        self._transport = transport
        self._response_verifier = response_verifier
        self._policy = policy

    def authenticate(self, request):
        request = validate_request(request)
        if not self._policy.enabled:
            raise AuthenticationTransportDisabledError("authentication transport is disabled")
        try:
            http_request = self._request_factory.create(request.authentication_request)
        except Exception as exc:
            raise AuthenticationRequestCreationError("authentication request creation failed") from exc
        if not isinstance(http_request, HTTPRequestOperation):
            raise AuthenticationRequestCreationError("request factory returned invalid request")
        try:
            prepared = self._pipeline.prepare(http_request)
        except Exception as exc:
            raise AuthenticationRequestCreationError("authentication request preparation failed") from exc
        if not isinstance(prepared, HTTPRequestOperation):
            raise AuthenticationRequestCreationError("pipeline returned invalid prepared request")
        try:
            raw_response = self._transport.send(prepared)
        except Exception as exc:
            raise AuthenticationRequestExecutionError("authentication request execution failed") from exc
        if not isinstance(raw_response, HTTPResponseOperation):
            raise AuthenticationRequestExecutionError("transport returned invalid response")
        try:
            response = self._pipeline.finalize(raw_response)
        except Exception as exc:
            raise AuthenticationRequestExecutionError("authentication response finalization failed") from exc
        if not isinstance(response, HTTPResponseOperation):
            raise AuthenticationRequestExecutionError("pipeline returned invalid finalized response")
        try:
            verification = self._response_verifier.verify(
                request.authentication_request, response)
        except Exception as exc:
            raise AuthenticationResponseVerificationError("authentication response verification failed") from exc
        if not isinstance(verification, AuthenticationVerificationResult):
            raise AuthenticationResponseVerificationError("response verifier returned invalid result")
        if not verification.success:
            return self._result(request, verification, None, response.response_id)
        try:
            authentication_result = self._authentication_service.authenticate(
                request.authentication_request)
        except Exception as exc:
            raise AuthenticationLifecycleError("authentication lifecycle failed") from exc
        if not isinstance(authentication_result, AuthenticationResult) or not authentication_result.success:
            raise AuthenticationLifecycleError("authentication service returned invalid result")
        return self._result(request, verification, authentication_result, response.response_id)

    def _result(self, request, verification, authentication_result, response_identifier):
        return AuthenticationTransportResult(
            request.attempt_id, verification.success, verification, authentication_result,
            response_identifier, request.context, self._policy.version,
            {"deterministic": True})
