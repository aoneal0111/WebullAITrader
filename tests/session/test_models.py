from dataclasses import FrozenInstanceError
import pytest

from app.session import Session, SessionIdentifier, SessionRequest, SessionSnapshot, SessionStatus


def test_identifier_and_request_are_injected_frozen_and_round_trip():
    identifier = SessionIdentifier("caller-session-1")
    request = SessionRequest(identifier, "trading", {"caller": "outer"})
    assert SessionIdentifier.from_dict(identifier.to_dict()) == identifier
    assert SessionRequest.from_dict(request.to_dict()) == request
    assert not hasattr(identifier, "__dict__")
    with pytest.raises(FrozenInstanceError):
        identifier.value = "generated"
    with pytest.raises(TypeError):
        request.metadata["x"] = True


def test_session_and_snapshot_round_trip():
    session = Session(SessionIdentifier("session-1"), "trading", SessionStatus.ACTIVE)
    snapshot = SessionSnapshot(SessionStatus.ACTIVE, session, (), 2)
    assert Session.from_dict(session.to_dict()) == session
    assert SessionSnapshot.from_dict(snapshot.to_dict()) == snapshot
    assert not hasattr(snapshot, "__dict__")


@pytest.mark.parametrize("value", ["", " session", "session "])
def test_identifier_validation(value):
    with pytest.raises(ValueError):
        SessionIdentifier(value)


def test_snapshot_requires_matching_state():
    session = Session(SessionIdentifier("session-1"), "trading", SessionStatus.CREATED)
    with pytest.raises(ValueError, match="must match"):
        SessionSnapshot(SessionStatus.ACTIVE, session, (), 1)
