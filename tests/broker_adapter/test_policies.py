from dataclasses import FrozenInstanceError
import json,pytest
from app.broker_adapter import *
from tests.broker_adapter.helpers import policy
def test_roundtrip_frozen():
 p=policy(metadata={"x":[1]});assert BrokerAdapterPolicy.from_dict(p.to_dict())==p;json.dumps(p.to_dict(),allow_nan=False)
 with pytest.raises(FrozenInstanceError):p.version="x"
@pytest.mark.parametrize("x",[{"adapter_enabled":1},{"allowed_order_types":()},{"maximum_quantity":-1},{"maximum_notional":-1}])
def test_invalid(x):
 with pytest.raises(ValueError):policy(**x)
