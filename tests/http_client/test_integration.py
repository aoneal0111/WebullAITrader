from app.http_client import HTTPClient
from app.http_runtime import HTTPRuntime,HTTPRuntimePolicy
from app.transport_runtime import TransportRequest,TransportResponse,TransportRuntime,TransportRuntimePolicy
from tests.http_client.helpers import FakeTransport,policy
from tests.http_runtime.helpers import request as http_request,STAMP
class HTTPRuntimeBridge:
 def __init__(self):self.runtime=HTTPRuntime(HTTPClient(FakeTransport(),policy()),HTTPRuntimePolicy(runtime_enabled=True))
 def execute(self,r):
  record=self.runtime.execute(http_request(body=r.payload));return TransportResponse(success=True,result=record.response.body,error="",correlation_id=r.correlation_id,timestamp=record.completed_timestamp)
def test_transport_runtime_http_runtime_client_fake_transport():
 r=TransportRequest(operation="http.execute",payload={"x":1},timestamp=STAMP);record=TransportRuntime(HTTPRuntimeBridge(),TransportRuntimePolicy(runtime_enabled=True)).execute(r);assert record.response.result["ok"] is True
