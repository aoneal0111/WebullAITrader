import pytest

from app.authentication import AuthenticationStateSnapshot, AuthenticationStatus
from app.session import (
    SessionCreationError, SessionPolicy, validate_authentication_state,
    validate_dependencies, validate_request,
)
from tests.session.helpers import FakeAuthenticationService, request


def test_dependencies_request_and_authentication_snapshot_validate():
    auth = FakeAuthenticationService()
    assert validate_dependencies(auth, SessionPolicy())
    assert validate_request(request()) == request()
    snapshot = AuthenticationStateSnapshot(AuthenticationStatus.AUTHENTICATED, 1)
    assert validate_authentication_state(snapshot) == snapshot


@pytest.mark.parametrize("auth,policy", [
    (object(), SessionPolicy()), (FakeAuthenticationService(), object()),
])
def test_invalid_dependencies_are_rejected(auth, policy):
    with pytest.raises(SessionCreationError):
        validate_dependencies(auth, policy)


def test_invalid_request_and_snapshot_are_rejected():
    with pytest.raises(SessionCreationError):
        validate_request(object())
    with pytest.raises(SessionCreationError):
        validate_authentication_state(object())
