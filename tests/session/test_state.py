import pytest

from app.session import InvalidSessionStateError, SessionState, SessionStatus
from tests.session.helpers import request


def test_state_follows_legal_lifecycle():
    state = SessionState()
    assert state.snapshot().status is SessionStatus.NO_SESSION
    assert state.create(request()).status is SessionStatus.CREATED
    assert state.transition(SessionStatus.ACTIVE).status is SessionStatus.ACTIVE
    final = state.transition(SessionStatus.INVALIDATED)
    assert final.status is SessionStatus.INVALIDATED
    assert final.transition_number == 3


def test_duplicate_activation_and_illegal_create_are_rejected_without_mutation():
    state = SessionState()
    state.create(request())
    state.transition(SessionStatus.ACTIVE)
    before = state.snapshot()
    with pytest.raises(InvalidSessionStateError):
        state.transition(SessionStatus.ACTIVE)
    with pytest.raises(InvalidSessionStateError):
        state.create(request("session-2"))
    assert state.snapshot() == before
