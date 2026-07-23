from typing import Protocol

from app.authentication.models import AuthenticationRequest, AuthenticationResult, AuthenticationStateSnapshot
from app.credentials import CredentialResponse


class AuthenticationService(Protocol):
    def authenticate(self, request: AuthenticationRequest) -> AuthenticationResult: ...
    def logout(self) -> AuthenticationStateSnapshot: ...
    def state(self) -> AuthenticationStateSnapshot: ...


class AuthenticationVerifier(Protocol):
    def verify(self, request: AuthenticationRequest, credentials: CredentialResponse) -> bool: ...
