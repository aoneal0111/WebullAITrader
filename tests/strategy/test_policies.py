from dataclasses import FrozenInstanceError
import pytest
from app.strategy import *
def test_policy_safe_default_frozen_roundtrip():
 p=StrategyPolicy();assert not p.enabled and p.strict_validation and not hasattr(p,"__dict__") and StrategyPolicy.from_dict(p.to_dict())==p
 with pytest.raises(FrozenInstanceError):p.enabled=True
@pytest.mark.parametrize("kwargs",[{"enabled":1},{"strict_validation":0},{"version":""}])
def test_policy_validation(kwargs):
 with pytest.raises(StrategyValidationError):StrategyPolicy(**kwargs)
