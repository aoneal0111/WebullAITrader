from app.authentication import AuthenticationStateSnapshot, AuthenticationStatus
from app.session import SessionIdentifier, SessionRequest


class FakeAuthenticationService:
    def __init__(self, status=AuthenticationStatus.AUTHENTICATED, error=None):
        self.status = status
        self.error = error
        self.state_calls = 0
        self.authentication_calls = 0

    def authenticate(self, request):
        self.authentication_calls += 1
        raise AssertionError("session manager must not authenticate")

    def logout(self):
        raise AssertionError("session manager must not log out authentication")

    def state(self):
        self.state_calls += 1
        if self.error:
            raise self.error
        return AuthenticationStateSnapshot(self.status, 0)


def request(identifier="session-1"):
    return SessionRequest(SessionIdentifier(identifier), "trading")
