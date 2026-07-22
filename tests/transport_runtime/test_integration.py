from app.transport_runtime import *
from app.webull_gateway import LoginRequest
from tests.transport_runtime.helpers import FakeExecutor,policy,STAMP
def test_gateway_protocol_payload_through_runtime():
 login=LoginRequest(STAMP,"production-live");r=TransportRequest(operation="gateway.authenticate",payload=login.to_dict(),timestamp=STAMP);record=TransportRuntime(FakeExecutor(),policy()).execute(r);assert record.response.result["authenticated"] is True
