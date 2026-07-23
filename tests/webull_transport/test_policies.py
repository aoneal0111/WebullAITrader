from dataclasses import FrozenInstanceError
import json,pytest
from app.webull_transport import *
from tests.webull_transport.helpers import policy
def test_roundtrip_frozen():
 p=policy();assert WebullTransportPolicy.from_dict(p.to_dict())==p;json.dumps(p.to_dict(),allow_nan=False)
 with pytest.raises(FrozenInstanceError):p.version="x"
@pytest.mark.parametrize("x",[{"transport_enabled":1},{"maximum_quantity":-1},{"maximum_notional":-1},{"allowed_symbols":("AAPL","aapl")},{"required_environment":""}])
def test_invalid(x):
 with pytest.raises(ValueError):policy(**x)
