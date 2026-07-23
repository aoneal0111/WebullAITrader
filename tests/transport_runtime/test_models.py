from dataclasses import FrozenInstanceError
import json,pytest
from app.transport_runtime import *
from tests.transport_runtime.helpers import request,FakeExecutor,policy
def test_request_ids_and_roundtrip():
 r=request(metadata={"x":[1]});assert r.request_id==request(metadata={"x":[1]}).request_id and TransportRequest.from_dict(r.to_dict())==r;json.dumps(r.to_dict(),allow_nan=False)
 with pytest.raises(FrozenInstanceError):r.operation="x"
def test_record_roundtrip():
 x=TransportRuntime(FakeExecutor(),policy()).execute(request());assert TransportExecutionRecord.from_dict(x.to_dict())==x and x.duration==2
