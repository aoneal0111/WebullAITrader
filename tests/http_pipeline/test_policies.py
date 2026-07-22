from dataclasses import FrozenInstanceError
import pytest

from app.http_pipeline import PipelinePolicy


def test_policy_is_frozen_slotted_and_round_trips():
    policy = PipelinePolicy(metadata={"deterministic": True})
    assert PipelinePolicy.from_dict(policy.to_dict()) == policy
    assert not hasattr(policy, "__dict__")
    with pytest.raises(FrozenInstanceError):
        policy.normalize_headers = False
    with pytest.raises(TypeError):
        policy.metadata["x"] = True


@pytest.mark.parametrize("kwargs", [
    {"version": ""}, {"normalize_headers": 1},
    {"normalize_query_order": 0}, {"strict_validation": 1},
])
def test_policy_validation(kwargs):
    with pytest.raises(ValueError):
        PipelinePolicy(**kwargs)
