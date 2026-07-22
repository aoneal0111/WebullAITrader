from dataclasses import FrozenInstanceError
import json,pytest
from types import MappingProxyType
from app.broker_execution import BrokerExecutionAuthorization,BrokerExecutionRequest
from app.broker_execution import ExecutionSafetyGate
from tests.broker_execution.helpers import request,snapshot
def test_models_round_trip_and_immutable():
    r=request(metadata={"x":[1]});assert BrokerExecutionRequest.from_dict(r.to_dict())==r
    a=ExecutionSafetyGate().authorize(r);assert BrokerExecutionAuthorization.from_dict(a.to_dict())==a;json.dumps(a.to_dict(),allow_nan=False)
    assert isinstance(r.metadata,MappingProxyType) and isinstance(r.account_snapshot.symbol_positions,MappingProxyType)
    with pytest.raises(FrozenInstanceError):a.reason=None
def test_snapshot_rejects_naive_time():
    from datetime import datetime
    with pytest.raises(ValueError):snapshot(timestamp=datetime(2026,1,1))
