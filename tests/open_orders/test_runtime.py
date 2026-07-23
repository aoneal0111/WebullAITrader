import pytest
from app.open_orders import *
from app.session import SessionSnapshot,SessionStatus
from tests.open_orders.fixtures import enabled_policy,request
from tests.open_orders.helpers import FakeGateway,FakeSessionManager,order,orders
def runtime(session=None,gateway=None,policy=None):return DeterministicOpenOrdersRuntime(session or FakeSessionManager(),gateway or FakeGateway(),policy or enabled_policy())
def test_construction_no_work():
 s,g=FakeSessionManager(),FakeGateway();runtime(s,g);assert s.calls==0 and not g.requests
def test_success_exactly_one_lookup_and_preserved_order():
 s,g=FakeSessionManager(),FakeGateway();result=runtime(s,g).get_open_orders(request());assert result.orders==orders() and tuple(x.broker_order_id for x in result.orders)==("broker-1","broker-2") and s.calls==1 and g.requests==[request()]
def test_empty_result_success():assert runtime(gateway=FakeGateway(())).get_open_orders(request()).success
def test_disabled_no_calls():
 s,g=FakeSessionManager(),FakeGateway();result=runtime(s,g,OpenOrdersPolicy()).get_open_orders(request());assert result.decision is OpenOrdersDecision.DISABLED and s.calls==0 and not g.requests
def test_invalid_session():
 s,g=FakeSessionManager(SessionSnapshot(SessionStatus.NO_SESSION,None,(),0)),FakeGateway();result=runtime(s,g).get_open_orders(request());assert result.decision is OpenOrdersDecision.SESSION_INVALID and s.calls==1 and not g.requests
def test_gateway_failure_no_repeat():
 g=FakeGateway(error=OSError("synthetic"));result=runtime(gateway=g).get_open_orders(request());assert result.decision is OpenOrdersDecision.GATEWAY_FAILURE and len(g.requests)==1
def test_account_and_duplicate_identity_rejection():
 with pytest.raises(OpenOrdersIdentityError):runtime(gateway=FakeGateway((order(account_id="other"),))).get_open_orders(request())
 with pytest.raises(OpenOrdersSnapshotError):runtime(gateway=FakeGateway((order(),order(client_id="client-2")))).get_open_orders(request())
 with pytest.raises(OpenOrdersSnapshotError):runtime(gateway=FakeGateway((order(),order("broker-2","client-1")))).get_open_orders(request())
def test_invalid_outputs_and_session_cause():
 with pytest.raises(OpenOrdersDependencyError):runtime(gateway=FakeGateway([order()])).get_open_orders(request())
 with pytest.raises(OpenOrdersDependencyError):runtime(session=FakeSessionManager(snapshot="bad")).get_open_orders(request())
 with pytest.raises(OpenOrdersDependencyError) as caught:runtime(session=FakeSessionManager(error=LookupError("synthetic"))).get_open_orders(request())
 assert isinstance(caught.value.__cause__,LookupError)
def test_equivalent_determinism_input_immutable():
 value=request();before=value.to_dict();assert runtime().get_open_orders(value)==runtime().get_open_orders(value) and value.to_dict()==before
