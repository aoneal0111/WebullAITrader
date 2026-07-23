from app.broker_adapter import BrokerAdapter,BrokerExecutionStatus
from app.webull_transport import WebullTransport,WebullTransportPolicy,WebullTransportState
from tests.broker_adapter.helpers import request as adapter_request,policy as adapter_policy,STAMP
from tests.webull_gateway.helpers import FakeGateway
def test_adapter_transport_protocol_fake_integration():
 g=FakeGateway();transport=WebullTransport(g,WebullTransportPolicy(transport_enabled=True,maximum_quantity=100,maximum_notional=20000,allowed_symbols=("AAPL",)),WebullTransportState(STAMP),STAMP);result=BrokerAdapter(transport).execute(adapter_request(policy=adapter_policy()));assert result.status is BrokerExecutionStatus.SUBMITTED and len(g.received)==1
