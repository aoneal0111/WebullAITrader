from typing import Protocol

from app.authentication import AuthenticationRequest
from app.authentication_transport.models import (
    AuthenticationTransportRequest, AuthenticationTransportResult, AuthenticationVerificationResult,
)
from app.http_pipeline import HTTPRequestOperation, HTTPResponseOperation


class AuthenticationTransportConnector(Protocol):
    def authenticate(self, request: AuthenticationTransportRequest) -> AuthenticationTransportResult: ...


class AuthenticationRequestFactory(Protocol):
    def create(self, authentication_request: AuthenticationRequest) -> HTTPRequestOperation: ...


class AuthenticationResponseVerifier(Protocol):
    def verify(self, authentication_request: AuthenticationRequest,
               http_response: HTTPResponseOperation) -> AuthenticationVerificationResult: ...
