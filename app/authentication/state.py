from app.authentication.exceptions import InvalidAuthenticationStateError
from app.authentication.models import AuthenticationStateSnapshot, AuthenticationStatus


class AuthenticationState:
    """Deterministic process-local state; snapshots are immutable."""

    _ALLOWED = {
        AuthenticationStatus.UNAUTHENTICATED: (AuthenticationStatus.AUTHENTICATING,),
        AuthenticationStatus.AUTHENTICATING: (
            AuthenticationStatus.AUTHENTICATED, AuthenticationStatus.UNAUTHENTICATED),
        AuthenticationStatus.AUTHENTICATED: (
            AuthenticationStatus.LOGGED_OUT, AuthenticationStatus.AUTHENTICATING),
        AuthenticationStatus.LOGGED_OUT: (AuthenticationStatus.AUTHENTICATING,),
    }

    def __init__(self):
        self._status = AuthenticationStatus.UNAUTHENTICATED
        self._transition_number = 0

    def transition(self, target):
        if not isinstance(target, AuthenticationStatus):
            raise InvalidAuthenticationStateError("target must be AuthenticationStatus")
        if target not in self._ALLOWED[self._status]:
            raise InvalidAuthenticationStateError(
                f"invalid authentication transition: {self._status.value} -> {target.value}")
        self._status = target
        self._transition_number += 1
        return self.snapshot()

    def snapshot(self):
        return AuthenticationStateSnapshot(
            self._status, self._transition_number, {"deterministic": True})
