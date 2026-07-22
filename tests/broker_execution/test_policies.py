from dataclasses import FrozenInstanceError
import json,pytest
from app.broker_execution import ExecutionSafetyPolicy
from tests.broker_execution.helpers import policy
def test_policy_round_trip_frozen():
    p=policy(metadata={"x":[1]});assert ExecutionSafetyPolicy.from_dict(p.to_dict())==p;json.dumps(p.to_dict(),allow_nan=False)
    with pytest.raises(FrozenInstanceError):p.version="x"
@pytest.mark.parametrize("x",[{"kill_switch_active":1},{"maximum_order_quantity":-1},{"maximum_order_notional":-1},{"allowed_symbols":("AAPL","aapl")},{"duplicate_window_seconds":-1}])
def test_invalid_policy(x):
    with pytest.raises(ValueError):policy(**x)
