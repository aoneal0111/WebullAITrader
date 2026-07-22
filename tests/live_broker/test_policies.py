from dataclasses import FrozenInstanceError
import json,pytest
from app.live_broker import LiveExecutionPolicy
from tests.live_broker.helpers import policy
def test_roundtrip_frozen():
 p=policy(metadata={"x":[1]});assert LiveExecutionPolicy.from_dict(p.to_dict())==p;json.dumps(p.to_dict(),allow_nan=False)
 with pytest.raises(FrozenInstanceError):p.version="x"
@pytest.mark.parametrize("x",[{"live_execution_enabled":1},{"maximum_order_quantity":-1},{"maximum_order_notional":-1},{"allowed_symbols":("AAPL","aapl")},{"required_environment":""}])
def test_invalid(x):
 with pytest.raises(ValueError):policy(**x)
