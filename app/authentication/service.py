from app.authentication.exceptions import (
    AuthenticationFailedError, AuthenticationProviderError, InvalidAuthenticationStateError,
)
from app.authentication.models import AuthenticationResult, AuthenticationStatus
from app.authentication.policies import AuthenticationPolicy
from app.authentication.state import AuthenticationState
from app.authentication.validation import validate_authentication_request, validate_dependencies
from app.credentials import CredentialRequest, CredentialResponse


class DeterministicAuthenticationService:
    def __init__(self, credential_provider, verifier, policy: AuthenticationPolicy):
        validate_dependencies(credential_provider, verifier, policy)
        self._credential_provider = credential_provider
        self._verifier = verifier
        self._policy = policy
        self._state = AuthenticationState()

    def authenticate(self, request):
        request = validate_authentication_request(request)
        current = self._state.snapshot().status
        if current is AuthenticationStatus.AUTHENTICATED and not self._policy.allow_reauthentication:
            raise InvalidAuthenticationStateError("reauthentication is disabled")
        if current is AuthenticationStatus.AUTHENTICATING:
            raise InvalidAuthenticationStateError("authentication is already in progress")
        self._state.transition(AuthenticationStatus.AUTHENTICATING)
        credential_request = CredentialRequest(
            request.broker_identifier, request.credential_purpose,
            request.required_value_names, request.metadata)
        try:
            credentials = self._credential_provider.provide(credential_request)
        except Exception as exc:
            self._state.transition(AuthenticationStatus.UNAUTHENTICATED)
            raise AuthenticationProviderError("credential provider failed") from exc
        if not isinstance(credentials, CredentialResponse):
            self._state.transition(AuthenticationStatus.UNAUTHENTICATED)
            raise AuthenticationProviderError("credential provider returned invalid response")
        try:
            verified = self._verifier.verify(request, credentials)
        except Exception as exc:
            self._state.transition(AuthenticationStatus.UNAUTHENTICATED)
            raise AuthenticationProviderError("authentication verifier failed") from exc
        if not isinstance(verified, bool):
            self._state.transition(AuthenticationStatus.UNAUTHENTICATED)
            raise AuthenticationProviderError("authentication verifier must return boolean")
        if not verified:
            state = self._state.transition(AuthenticationStatus.UNAUTHENTICATED)
            raise AuthenticationFailedError(
                f"authentication failed in state {state.status.value}")
        state = self._state.transition(AuthenticationStatus.AUTHENTICATED)
        return AuthenticationResult(True, state, "AUTHENTICATED", self._policy.version,
                                    {"deterministic": True})

    def logout(self):
        if self._state.snapshot().status is not AuthenticationStatus.AUTHENTICATED:
            raise InvalidAuthenticationStateError("logout requires authenticated state")
        return self._state.transition(AuthenticationStatus.LOGGED_OUT)

    def state(self):
        return self._state.snapshot()
