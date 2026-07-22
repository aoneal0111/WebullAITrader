from app.broker_adapter import *
from tests.broker_adapter.helpers import FakeTransport,request
def test_ready_invocation_to_fake_transport_result():
 t=FakeTransport();result=BrokerAdapter(t).execute(request());assert result.status is BrokerExecutionStatus.SUBMITTED and len(t.requests)==1 and isinstance(t.requests[0],BrokerOrderRequest)
