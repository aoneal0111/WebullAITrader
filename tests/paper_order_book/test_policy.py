from dataclasses import FrozenInstanceError

import pytest

from app.paper_order_book import (
    PaperOrderBookPolicy,
    PaperOrderBookValidationError,
    serialize_policy,
)


def test_policy_defaults_enforce_deterministic_validation() -> None:
    policy = PaperOrderBookPolicy()
    assert policy.reject_duplicate_command_ids is True
    assert policy.reject_non_monotonic_timestamps is True
    assert serialize_policy(policy) == {
        "reject_duplicate_command_ids": True,
        "reject_non_monotonic_timestamps": True,
    }


def test_policy_is_frozen_and_requires_exact_booleans() -> None:
    policy = PaperOrderBookPolicy()
    with pytest.raises(FrozenInstanceError):
        policy.reject_duplicate_command_ids = False
    with pytest.raises(PaperOrderBookValidationError):
        PaperOrderBookPolicy(reject_duplicate_command_ids=1)
