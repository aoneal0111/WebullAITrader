from dataclasses import FrozenInstanceError
import json
from types import MappingProxyType

import pytest

from app.outcomes import OutcomePolicy


def test_policy_frozen_immutable_and_round_trip():
    policy = OutcomePolicy(metadata={"nested": [1]})
    assert isinstance(policy.metadata, MappingProxyType)
    assert OutcomePolicy.from_dict(policy.to_dict()) == policy
    json.dumps(policy.to_dict(), allow_nan=False)
    with pytest.raises(FrozenInstanceError):
        policy.version = "changed"


@pytest.mark.parametrize("changes", [{"version": ""}, {"include_checks": 1},
                                      {"include_execution_metadata": "yes"}, {"metadata": {"x": object()}}])
def test_policy_validation(changes):
    with pytest.raises(ValueError):
        OutcomePolicy(**changes)
