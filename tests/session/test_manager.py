import pytest

from app.authentication import AuthenticationStatus
from app.session import (
    DeterministicSessionManager, InvalidSessionStateError, SessionCreationError,
    SessionPolicy, SessionReplacementError, SessionStatus,
)
from tests.session.helpers import FakeAuthenticationService, request


def manager(auth=None, **policy):
    return DeterministicSessionManager(
        auth or FakeAuthenticationService(), SessionPolicy(**policy))


def test_create_activate_invalidate_and_state():
    auth = FakeAuthenticationService()
    subject = manager(auth)
    assert subject.state().status is SessionStatus.NO_SESSION
    assert subject.create(request()).status is SessionStatus.CREATED
    assert subject.activate().status is SessionStatus.ACTIVE
    assert subject.invalidate().status is SessionStatus.INVALIDATED
    assert auth.state_calls == 1 and auth.authentication_calls == 0


def test_create_requires_authenticated_dependency():
    subject = manager(FakeAuthenticationService(AuthenticationStatus.UNAUTHENTICATED))
    with pytest.raises(SessionCreationError, match="authenticated state"):
        subject.create(request())
    assert subject.state().status is SessionStatus.NO_SESSION


def test_duplicate_activation_and_repeated_invalidation_rejected():
    subject = manager()
    subject.create(request())
    subject.activate()
    with pytest.raises(InvalidSessionStateError):
        subject.activate()
    subject.invalidate()
    with pytest.raises(InvalidSessionStateError):
        subject.invalidate()


def test_replacement_disabled_by_default():
    subject = manager()
    subject.create(request())
    with pytest.raises(SessionReplacementError, match="disabled"):
        subject.replace(request("session-2"))


def test_replacement_records_previous_identifier_and_creates_new_session():
    subject = manager(allow_replacement=True)
    subject.create(request("session-1"))
    subject.activate()
    snapshot = subject.replace(request("session-2"))
    assert snapshot.status is SessionStatus.CREATED
    assert snapshot.session.identifier.value == "session-2"
    assert tuple(value.value for value in snapshot.replaced_identifiers) == ("session-1",)
    assert snapshot.transition_number == 3


def test_replacement_identifier_must_be_new_and_existing_session_required():
    subject = manager(allow_replacement=True)
    with pytest.raises(SessionReplacementError, match="existing"):
        subject.replace(request("session-2"))
    subject.create(request("session-1"))
    with pytest.raises(SessionReplacementError, match="must be new"):
        subject.replace(request("session-1"))


def test_authentication_dependency_failure_is_normalized():
    subject = manager(FakeAuthenticationService(error=LookupError()))
    with pytest.raises(SessionCreationError, match="unavailable"):
        subject.create(request())
