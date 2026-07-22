from dataclasses import FrozenInstanceError
import pytest
from app.webull_authentication_config import WebullAuthenticationConfigurationLoaderPolicy
def test_loader_policy_frozen_roundtrip():
 p=WebullAuthenticationConfigurationLoaderPolicy();assert p.strict_unknown_fields;assert WebullAuthenticationConfigurationLoaderPolicy.from_dict(p.to_dict())==p
 with pytest.raises(FrozenInstanceError):p.strict_unknown_fields=False
@pytest.mark.parametrize("kwargs",[{"version":""},{"strict_unknown_fields":1}])
def test_loader_policy_invalid(kwargs):
 with pytest.raises(ValueError):WebullAuthenticationConfigurationLoaderPolicy(**kwargs)
