from dataclasses import FrozenInstanceError
from decimal import Decimal
import pytest

from app.httpx_transport import HTTPXTransportPolicy


def test_policy_safe_defaults_frozen_slotted_and_round_trip():
    policy = HTTPXTransportPolicy(metadata={"deterministic": True})
    assert not policy.enabled and not policy.follow_redirects
    assert HTTPXTransportPolicy.from_dict(policy.to_dict()) == policy
    assert not hasattr(policy, "__dict__")
    with pytest.raises(FrozenInstanceError):
        policy.enabled = True
    with pytest.raises(TypeError):
        policy.metadata["x"] = True


@pytest.mark.parametrize("kwargs", [
    {"version": ""}, {"enabled": 1}, {"timeout_seconds": Decimal("0")},
    {"timeout_seconds": Decimal("Infinity")}, {"timeout_seconds": 1},
    {"follow_redirects": 1}, {"verify_response_type": 0},
])
def test_policy_validation(kwargs):
    with pytest.raises(ValueError):
        HTTPXTransportPolicy(**kwargs)
