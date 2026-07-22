from dataclasses import FrozenInstanceError
import json
from types import MappingProxyType

import pytest

from app.learning import LearningPolicy


def test_policy_is_frozen_immutable_and_round_trips():
    policy = LearningPolicy(metadata={"nested": [1]})
    assert isinstance(policy.metadata, MappingProxyType)
    assert LearningPolicy.from_dict(policy.to_dict()) == policy
    json.dumps(policy.to_dict(), allow_nan=False)
    with pytest.raises(FrozenInstanceError):
        policy.version = "changed"


@pytest.mark.parametrize("changes", [{"version": ""}, {"minimum_sample_size": 0},
    {"minimum_sample_size": True}, {"include_trade_statistics": 1},
    {"include_risk_statistics": "yes"}, {"include_strategy_statistics": None},
    {"metadata": {"bad": object()}}])
def test_policy_validation(changes):
    with pytest.raises(ValueError):
        LearningPolicy(**changes)
