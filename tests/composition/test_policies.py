from dataclasses import FrozenInstanceError
import pytest

from app.composition import CompositionPolicy


def test_policy_is_frozen_slotted_and_round_trips():
    policy = CompositionPolicy(metadata={"owner": "caller"})
    assert CompositionPolicy.from_dict(policy.to_dict()) == policy
    assert not hasattr(policy, "__dict__")
    with pytest.raises(FrozenInstanceError):
        policy.version = "changed"
    with pytest.raises(TypeError):
        policy.metadata["x"] = True


@pytest.mark.parametrize("kwargs", [
    {"version": ""}, {"strict_validation": 1}, {"allow_overrides": 0},
])
def test_policy_rejects_invalid_fields(kwargs):
    with pytest.raises(ValueError):
        CompositionPolicy(**kwargs)
