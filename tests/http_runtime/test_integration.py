from app.http_runtime import HTTPRuntime
from app.transport_runtime import TransportRequest,TransportResponse,TransportRuntime,TransportRuntimePolicy
from tests.http_runtime.helpers import FakeHTTPExecutor,policy,request,STAMP
class HTTPBridge:
 def __init__(self):self.http=HTTPRuntime(FakeHTTPExecutor(),policy())
 def execute(self,r):
  record=self.http.execute(request(body=r.payload));return TransportResponse(success=True,result=record.response.body,error="",correlation_id=r.correlation_id,timestamp=record.completed_timestamp)
def test_transport_runtime_to_http_protocol_fake():
 r=TransportRequest(operation="http.execute",payload={"x":1},timestamp=STAMP);record=TransportRuntime(HTTPBridge(),TransportRuntimePolicy(runtime_enabled=True)).execute(r);assert record.response.result["ok"] is True
