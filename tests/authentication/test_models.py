from dataclasses import FrozenInstanceError
import pytest

from app.authentication import (
    AuthenticationRequest, AuthenticationResult, AuthenticationStateSnapshot, AuthenticationStatus,
)


def test_request_is_frozen_slotted_immutable_and_round_trips():
    value = AuthenticationRequest("broker", "sign-in", ("identity",), {"caller": "outer"})
    assert AuthenticationRequest.from_dict(value.to_dict()) == value
    assert not hasattr(value, "__dict__")
    with pytest.raises(FrozenInstanceError):
        value.credential_purpose = "other"
    with pytest.raises(TypeError):
        value.metadata["x"] = True


def test_snapshot_and_result_round_trip():
    snapshot = AuthenticationStateSnapshot(AuthenticationStatus.AUTHENTICATED, 2)
    result = AuthenticationResult(True, snapshot, "AUTHENTICATED", "authentication_policy_v1")
    assert AuthenticationStateSnapshot.from_dict(snapshot.to_dict()) == snapshot
    assert AuthenticationResult.from_dict(result.to_dict()) == result
    assert not hasattr(result, "__dict__")


@pytest.mark.parametrize("args", [
    ("", "purpose", ("value",)), ("broker", "", ("value",)),
    ("broker", "purpose", ()), ("broker", "purpose", ("value", "value")),
])
def test_request_validation(args):
    with pytest.raises(ValueError):
        AuthenticationRequest(*args)


def test_result_state_must_match_success():
    snapshot = AuthenticationStateSnapshot(AuthenticationStatus.UNAUTHENTICATED, 0)
    with pytest.raises(ValueError, match="does not match"):
        AuthenticationResult(True, snapshot, "INVALID", "v1")
