from dataclasses import FrozenInstanceError
import pytest
from app.authentication_runtime import AuthenticationRuntimePolicy
def test_policy_disabled_frozen_roundtrip():
 p=AuthenticationRuntimePolicy();assert not p.enabled;assert AuthenticationRuntimePolicy.from_dict(p.to_dict())==p;assert not hasattr(p,"__dict__")
 with pytest.raises(FrozenInstanceError):p.enabled=True
@pytest.mark.parametrize("kwargs",[{"version":""},{"enabled":1},{"strict_validation":0}])
def test_policy_validation(kwargs):
 with pytest.raises(ValueError):AuthenticationRuntimePolicy(**kwargs)
