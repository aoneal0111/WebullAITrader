from dataclasses import FrozenInstanceError
import pytest
from app.webull_authentication import WebullAuthenticationPolicy
def test_policy_disabled_frozen_roundtrip():
 p=WebullAuthenticationPolicy();assert not p.enabled;assert WebullAuthenticationPolicy.from_dict(p.to_dict())==p;assert not hasattr(p,"__dict__")
 with pytest.raises(FrozenInstanceError):p.enabled=True
@pytest.mark.parametrize("k",["enabled","strict_validation","include_device_identifier","require_success_indicator","reject_unexpected_success_values"])
def test_bool_validation(k):
 with pytest.raises(ValueError):WebullAuthenticationPolicy(**{k:1})
