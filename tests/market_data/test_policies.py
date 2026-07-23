from dataclasses import FrozenInstanceError
import pytest
from app.market_data import *
def test_safe_policy_frozen_roundtrip():
 p=MarketDataPolicy();assert not p.enabled and not hasattr(p,"__dict__") and MarketDataPolicy.from_dict(p.to_dict())==p
 with pytest.raises(FrozenInstanceError):p.enabled=True
 with pytest.raises(TypeError):p.metadata["x"]=1
@pytest.mark.parametrize("kwargs",[{"enabled":1},{"strict_validation":0},{"version":""}])
def test_policy_validation(kwargs):
 with pytest.raises(MarketDataValidationError):MarketDataPolicy(**kwargs)
