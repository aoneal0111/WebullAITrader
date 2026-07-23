from dataclasses import FrozenInstanceError
import json,pytest
from app.transport_runtime import *
from tests.transport_runtime.helpers import policy
def test_roundtrip_frozen_default_disabled():
 p=TransportRuntimePolicy();assert not p.runtime_enabled and TransportRuntimePolicy.from_dict(p.to_dict())==p;json.dumps(p.to_dict(),allow_nan=False)
 with pytest.raises(FrozenInstanceError):p.version="x"
@pytest.mark.parametrize("x",[{"runtime_enabled":1},{"timeout_seconds":-1},{"timeout_seconds":0}])
def test_invalid(x):
 with pytest.raises(ValueError):policy(**x)
