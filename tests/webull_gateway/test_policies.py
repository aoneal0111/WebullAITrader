from dataclasses import FrozenInstanceError
import json,pytest
from app.webull_gateway import WebullGatewayPolicy
def test_policy_roundtrip_frozen_default_disabled():
 p=WebullGatewayPolicy();assert not p.gateway_enabled and WebullGatewayPolicy.from_dict(p.to_dict())==p;json.dumps(p.to_dict(),allow_nan=False)
 with pytest.raises(FrozenInstanceError):p.version="x"
@pytest.mark.parametrize("x",[{"gateway_enabled":1},{"required_environment":""}])
def test_invalid(x):
 with pytest.raises(ValueError):WebullGatewayPolicy(**x)
