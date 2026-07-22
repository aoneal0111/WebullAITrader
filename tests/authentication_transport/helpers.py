from app.authentication import (
    AuthenticationRequest, AuthenticationResult, AuthenticationStateSnapshot, AuthenticationStatus,
)
from app.authentication_transport import (
    AuthenticationTransportContext, AuthenticationTransportRequest,
    AuthenticationVerificationResult,
)
from app.http_pipeline import HTTPRequestOperation, HTTPResponseOperation, PipelineContext
from app.http_runtime import HTTPMethod


def authentication_request():
    return AuthenticationRequest("broker", "sign-in", ("identity",))


def connector_request():
    return AuthenticationTransportRequest(
        "attempt-1", authentication_request(), AuthenticationTransportContext("correlation-1"))


def http_request():
    return HTTPRequestOperation(
        "http-request-1", HTTPMethod.POST, "https://mock.invalid/auth", (), (),
        {"opaque": "input"}, PipelineContext("correlation-1"))


def http_response():
    return HTTPResponseOperation(
        "http-request-1:response", 200, (), {"accepted": True},
        PipelineContext("correlation-1"))


class FakeAuthenticationService:
    def __init__(self, error=None, result=None):
        self.error = error
        self.calls = []
        self._status = AuthenticationStatus.UNAUTHENTICATED
        self.result = result

    def authenticate(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        self._status = AuthenticationStatus.AUTHENTICATED
        return self.result or AuthenticationResult(
            True, AuthenticationStateSnapshot(AuthenticationStatus.AUTHENTICATED, 2),
            "AUTHENTICATED", "authentication_policy_v1")

    def logout(self):
        self._status = AuthenticationStatus.LOGGED_OUT

    def state(self):
        return AuthenticationStateSnapshot(self._status, len(self.calls) * 2)


class FakeRequestFactory:
    def __init__(self, result=None, error=None):
        self.result = result or http_request()
        self.error = error
        self.calls = []

    def create(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return self.result


class FakePipeline:
    def __init__(self, prepare_error=None, finalize_error=None):
        self.prepare_error = prepare_error
        self.finalize_error = finalize_error
        self.prepared = []
        self.finalized = []

    def prepare(self, request):
        self.prepared.append(request)
        if self.prepare_error:
            raise self.prepare_error
        return request

    def finalize(self, response):
        self.finalized.append(response)
        if self.finalize_error:
            raise self.finalize_error
        return response


class FakeTransport:
    def __init__(self, result=None, error=None):
        self.result = result or http_response()
        self.error = error
        self.calls = []

    def send(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return self.result


class FakeResponseVerifier:
    def __init__(self, result=None, error=None):
        self.result = result or AuthenticationVerificationResult(True, "VERIFIED")
        self.error = error
        self.calls = []

    def verify(self, request, response):
        self.calls.append((request, response))
        if self.error:
            raise self.error
        return self.result
