from dataclasses import FrozenInstanceError
import pytest

from app.session import SessionPolicy


def test_policy_is_frozen_slotted_and_round_trips():
    policy = SessionPolicy(metadata={"deterministic": True})
    assert not policy.allow_replacement
    assert SessionPolicy.from_dict(policy.to_dict()) == policy
    assert not hasattr(policy, "__dict__")
    with pytest.raises(FrozenInstanceError):
        policy.allow_replacement = True
    with pytest.raises(TypeError):
        policy.metadata["x"] = True


@pytest.mark.parametrize("kwargs", [
    {"version": ""}, {"allow_replacement": 1}, {"strict_validation": 0},
])
def test_policy_validation(kwargs):
    with pytest.raises(ValueError):
        SessionPolicy(**kwargs)
