from dataclasses import FrozenInstanceError
import pytest

from app.credentials import CredentialPolicy


def test_policy_safe_defaults_frozen_and_round_trip():
    policy = CredentialPolicy(metadata={"deterministic": True})
    assert not policy.provider_enabled
    assert CredentialPolicy.from_dict(policy.to_dict()) == policy
    assert not hasattr(policy, "__dict__")
    with pytest.raises(FrozenInstanceError):
        policy.provider_enabled = True
    with pytest.raises(TypeError):
        policy.metadata["x"] = True


@pytest.mark.parametrize("kwargs", [
    {"version": ""}, {"provider_enabled": 1},
    {"require_non_empty_values": 0}, {"allow_additional_values": 1},
])
def test_policy_validation(kwargs):
    with pytest.raises(ValueError):
        CredentialPolicy(**kwargs)
