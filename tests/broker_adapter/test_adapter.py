import pytest
from app.broker_adapter import *
from app.live_broker import LiveExecutionGuard,LiveExecutionPolicy
from tests.broker_adapter.helpers import *
from tests.live_broker.helpers import request as live_request
def test_submitted_deterministic_one_transport_call():
 t=FakeTransport();r=request();a=BrokerAdapter(t).execute(r);b=BrokerAdapter(FakeTransport()).execute(r);assert a==b and a.status is BrokerExecutionStatus.SUBMITTED and len(t.requests)==1
@pytest.mark.parametrize("changes,reason",[
 ({"policy":BrokerAdapterPolicy()},BrokerExecutionReason.ADAPTER_DISABLED),
 ({"policy":policy(maximum_quantity=1)},BrokerExecutionReason.QUANTITY_EXCEEDS_LIMIT),
 ({"policy":policy(maximum_notional=1)},BrokerExecutionReason.NOTIONAL_EXCEEDS_LIMIT),
 ({"order_type":BrokerOrderType.MARKET},BrokerExecutionReason.ORDER_TYPE_NOT_ALLOWED),
 ({"time_in_force":BrokerTimeInForce.GTC},BrokerExecutionReason.TIME_IN_FORCE_NOT_ALLOWED)])
def test_preflight_blocks_without_transport(changes,reason):
 t=FakeTransport();x=BrokerAdapter(t).execute(request(**changes));assert x.reason is reason and not t.requests
def test_duplicate_blocks():
 r=request();cid=BrokerOrderMapper().map(r).client_order_id;t=FakeTransport();x=BrokerAdapter(t).execute(request(invocation=r.invocation,state=BrokerAdapterState(STAMP,(cid,))));assert x.reason is BrokerExecutionReason.DUPLICATE_CLIENT_ORDER_ID and not t.requests
@pytest.mark.parametrize("transport,status,reason",[(FakeTransport(BrokerTransportStatus.REJECTED),BrokerExecutionStatus.REJECTED,BrokerExecutionReason.TRANSPORT_REJECTED),(FakeTransport(BrokerTransportStatus.FAILED),BrokerExecutionStatus.TRANSPORT_FAILED,BrokerExecutionReason.TRANSPORT_FAILURE),(FakeTransport(BrokerTransportStatus.UNKNOWN),BrokerExecutionStatus.UNKNOWN,BrokerExecutionReason.UNKNOWN_TRANSPORT_STATUS),(FakeTransport(mismatch=True),BrokerExecutionStatus.TRANSPORT_FAILED,BrokerExecutionReason.RESPONSE_CLIENT_ORDER_ID_MISMATCH),(FakeTransport(raise_error=True),BrokerExecutionStatus.TRANSPORT_FAILED,BrokerExecutionReason.TRANSPORT_FAILURE)])
def test_transport_normalization(transport,status,reason):
 x=BrokerAdapter(transport).execute(request());assert x.status is status and x.reason is reason
