from app.broker_adapter import BrokerAdapter,BrokerExecutionStatus
from tests.broker_adapter.helpers import request as adapter_request,policy as adapter_policy
from tests.webull_transport.helpers import *
def test_adapter_transport_gateway_pipeline():
 g=FakeGateway();w=transport(g);result=BrokerAdapter(w).execute(adapter_request(policy=adapter_policy()));assert result.status is BrokerExecutionStatus.SUBMITTED and len(g.commands)==1
