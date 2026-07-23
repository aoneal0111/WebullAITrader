from dataclasses import FrozenInstanceError
import json,pytest
from app.broker_adapter import *
from tests.broker_adapter.helpers import request,FakeTransport
def test_roundtrips_frozen():
 r=request(metadata={"x":[1]});assert BrokerAdapterRequest.from_dict(r.to_dict())==r
 x=BrokerAdapter(FakeTransport()).execute(r);assert BrokerLiveExecutionResult.from_dict(x.to_dict())==x;json.dumps(x.to_dict(),allow_nan=False)
 with pytest.raises(FrozenInstanceError):x.status=None
