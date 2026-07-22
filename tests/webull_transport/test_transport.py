import pytest
from app.broker_adapter import BrokerTransportStatus
from app.webull_transport import *
from tests.webull_transport.helpers import *
def test_success_once():
 g=FakeGateway();x=transport(g).submit_order(order());assert x.status is BrokerTransportStatus.ACCEPTED and len(g.commands)==1
@pytest.mark.parametrize("p,code",[(WebullTransportPolicy(),WebullRejectionCode.TRANSPORT_DISABLED),(policy(required_environment="other"),WebullRejectionCode.ENVIRONMENT_MISMATCH),(policy(allowed_symbols=("MSFT",)),WebullRejectionCode.SYMBOL_NOT_ALLOWED),(policy(maximum_quantity=1),WebullRejectionCode.QUANTITY_EXCEEDS_LIMIT),(policy(maximum_notional=1),WebullRejectionCode.NOTIONAL_EXCEEDS_LIMIT)])
def test_blockers(p,code):
 g=FakeGateway();x=transport(g,p).submit_order(order());assert x.rejection_code==code.value and not g.commands
def test_duplicate():
 o=order();r=WebullTransportRequest(o,STAMP,policy(),WebullTransportState(STAMP));tid=WebullOrderMapper().map(r).transport_request_id;g=FakeGateway();x=transport(g,state=WebullTransportState(STAMP,(tid,))).submit_order(o);assert x.rejection_code==WebullRejectionCode.DUPLICATE_TRANSPORT_REQUEST.value and not g.commands
@pytest.mark.parametrize("g,status",[(FakeGateway(WebullGatewayStatus.REJECTED),BrokerTransportStatus.REJECTED),(FakeGateway(WebullGatewayStatus.FAILED),BrokerTransportStatus.FAILED),(FakeGateway(WebullGatewayStatus.UNKNOWN),BrokerTransportStatus.UNKNOWN),(FakeGateway(fail=True),BrokerTransportStatus.FAILED),(FakeGateway(mismatch=True),BrokerTransportStatus.FAILED)])
def test_normalization(g,status):assert transport(g).submit_order(order()).status is status
