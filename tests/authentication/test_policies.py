from dataclasses import FrozenInstanceError
import pytest

from app.authentication import AuthenticationPolicy


def test_policy_is_frozen_slotted_and_round_trips():
    policy = AuthenticationPolicy(metadata={"deterministic": True})
    assert not policy.allow_reauthentication
    assert AuthenticationPolicy.from_dict(policy.to_dict()) == policy
    assert not hasattr(policy, "__dict__")
    with pytest.raises(FrozenInstanceError):
        policy.allow_reauthentication = True
    with pytest.raises(TypeError):
        policy.metadata["x"] = True


@pytest.mark.parametrize("kwargs", [
    {"version": ""}, {"allow_reauthentication": 1}, {"strict_state_validation": 0},
])
def test_policy_validation(kwargs):
    with pytest.raises(ValueError):
        AuthenticationPolicy(**kwargs)
