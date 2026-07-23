from dataclasses import FrozenInstanceError
import json,pytest
from app.http_runtime import *
from tests.http_runtime.helpers import *
def test_request_response_record_roundtrip():
 r=request();assert r.request_id==request().request_id and HTTPRequest.from_dict(r.to_dict())==r
 x=HTTPRuntime(FakeHTTPExecutor(),policy()).execute(r);assert HTTPExecutionRecord.from_dict(x.to_dict())==x;json.dumps(x.to_dict(),allow_nan=False)
 with pytest.raises(FrozenInstanceError):r.url="x"
