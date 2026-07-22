from dataclasses import FrozenInstanceError
import json,pytest
from app.live_broker import *
from tests.live_broker.helpers import request
def test_request_and_invocation_roundtrip_frozen():
 r=request(metadata={"x":[1]});assert LiveExecutionRequest.from_dict(r.to_dict())==r
 i=LiveExecutionGuard().authorize(r);assert LiveBrokerInvocation.from_dict(i.to_dict())==i;json.dumps(i.to_dict(),allow_nan=False)
 with pytest.raises(FrozenInstanceError):i.reason=None
