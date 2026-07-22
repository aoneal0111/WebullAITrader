from dataclasses import FrozenInstanceError
import pytest

from app.authentication_transport import AuthenticationTransportPolicy


def test_policy_disabled_default_frozen_and_round_trip():
    policy = AuthenticationTransportPolicy(metadata={"deterministic": True})
    assert not policy.enabled
    assert AuthenticationTransportPolicy.from_dict(policy.to_dict()) == policy
    assert not hasattr(policy, "__dict__")
    with pytest.raises(FrozenInstanceError):
        policy.enabled = True
    with pytest.raises(TypeError):
        policy.metadata["x"] = True


@pytest.mark.parametrize("kwargs", [
    {"version": ""}, {"enabled": 1}, {"strict_validation": 0},
    {"fail_authentication_on_transport_error": 1},
])
def test_policy_validation(kwargs):
    with pytest.raises(ValueError):
        AuthenticationTransportPolicy(**kwargs)
