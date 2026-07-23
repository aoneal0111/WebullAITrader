import pytest

from app.authentication import (
    AuthenticationState, AuthenticationStatus, InvalidAuthenticationStateError,
)


def test_legal_state_sequence_is_deterministic():
    state = AuthenticationState()
    assert state.snapshot().status is AuthenticationStatus.UNAUTHENTICATED
    assert state.transition(AuthenticationStatus.AUTHENTICATING).transition_number == 1
    assert state.transition(AuthenticationStatus.AUTHENTICATED).transition_number == 2
    assert state.transition(AuthenticationStatus.LOGGED_OUT).transition_number == 3


def test_failure_transition_returns_to_unauthenticated():
    state = AuthenticationState()
    state.transition(AuthenticationStatus.AUTHENTICATING)
    snapshot = state.transition(AuthenticationStatus.UNAUTHENTICATED)
    assert snapshot.status is AuthenticationStatus.UNAUTHENTICATED
    assert snapshot.transition_number == 2


def test_invalid_transition_is_rejected_without_mutation():
    state = AuthenticationState()
    before = state.snapshot()
    with pytest.raises(InvalidAuthenticationStateError):
        state.transition(AuthenticationStatus.LOGGED_OUT)
    assert state.snapshot() == before
