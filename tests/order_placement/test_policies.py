from dataclasses import FrozenInstanceError
import pytest
from app.order_placement import *
def test_policy_safe_frozen_roundtrip():
 p=OrderPlacementPolicy();assert not p.enabled and not hasattr(p,"__dict__") and OrderPlacementPolicy.from_dict(p.to_dict())==p
 with pytest.raises(FrozenInstanceError):p.enabled=True
 with pytest.raises(TypeError):p.metadata["x"]=1
@pytest.mark.parametrize("kwargs",[{"enabled":1},{"strict_validation":0},{"version":""}])
def test_policy_validation(kwargs):
 with pytest.raises(OrderPlacementValidationError):OrderPlacementPolicy(**kwargs)
