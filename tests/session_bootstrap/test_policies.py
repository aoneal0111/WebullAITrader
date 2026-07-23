from dataclasses import FrozenInstanceError
import pytest
from app.session_bootstrap import SessionBootstrapPolicy
def test_policy_disabled_frozen_roundtrip():
 p=SessionBootstrapPolicy();assert not p.enabled;assert SessionBootstrapPolicy.from_dict(p.to_dict())==p
 with pytest.raises(FrozenInstanceError):p.enabled=True
@pytest.mark.parametrize("kwargs",[{"version":""},{"enabled":1},{"strict_validation":0}])
def test_invalid_policy(kwargs):
 with pytest.raises(ValueError):SessionBootstrapPolicy(**kwargs)
