from dataclasses import FrozenInstanceError
import pytest
from app.portfolio import *
def test_defaults_frozen_roundtrip():
 p=PortfolioPolicy();assert not p.enabled and p.strict_validation and not hasattr(p,"__dict__") and PortfolioPolicy.from_dict(p.to_dict())==p
 with pytest.raises(FrozenInstanceError):p.enabled=True
@pytest.mark.parametrize("kwargs",[{"enabled":1},{"strict_validation":0},{"version":""}])
def test_invalid_policy(kwargs):
 with pytest.raises(PortfolioValidationError):PortfolioPolicy(**kwargs)
