import pytest
from app.order_placement import *
from app.session import SessionSnapshot,SessionStatus
from tests.order_placement.fixtures import enabled_policy,request
from tests.order_placement.helpers import FakeGateway,FakeSessionManager,acknowledgement,active_snapshot
def runtime(session=None,gateway=None,policy=None):return DeterministicOrderPlacementRuntime(session or FakeSessionManager(),gateway or FakeGateway(),policy or enabled_policy())
def test_construction_no_work():
 s,g=FakeSessionManager(),FakeGateway();runtime(s,g);assert s.calls==0 and not g.requests
def test_success_exactly_one_session_and_gateway_call():
 s,g=FakeSessionManager(),FakeGateway();result=runtime(s,g).place_order(request());assert result.success and result.broker_order_id=="broker-1" and s.calls==1 and g.requests==[request()]
def test_disabled_no_calls():
 s,g=FakeSessionManager(),FakeGateway();result=runtime(s,g,OrderPlacementPolicy()).place_order(request());assert result.decision is OrderPlacementDecision.DISABLED and s.calls==0 and not g.requests
@pytest.mark.parametrize("snapshot",[SessionSnapshot(SessionStatus.NO_SESSION,None,(),0),active_snapshot("other")])
def test_invalid_session(snapshot):
 s,g=FakeSessionManager(snapshot),FakeGateway();result=runtime(s,g).place_order(request());assert result.decision is OrderPlacementDecision.SESSION_INVALID and s.calls==1 and not g.requests
def test_rejection_and_gateway_failure_are_results_without_repeat():
 rejected=FakeGateway(acknowledgement(False));result=runtime(gateway=rejected).place_order(request());assert result.decision is OrderPlacementDecision.ORDER_REJECTED and len(rejected.requests)==1
 failed=FakeGateway(error=OSError("synthetic"));result=runtime(gateway=failed).place_order(request());assert result.decision is OrderPlacementDecision.GATEWAY_FAILURE and len(failed.requests)==1
def test_invalid_outputs_and_mismatch():
 with pytest.raises(OrderPlacementDependencyError):runtime(session=FakeSessionManager(snapshot="bad")).place_order(request())
 with pytest.raises(OrderPlacementDependencyError):runtime(gateway=FakeGateway(response="bad")).place_order(request())
 with pytest.raises(OrderPlacementDependencyError):runtime(gateway=FakeGateway(response=BrokerOrderAcknowledgement("other","broker",True,NormalizedOrderStatus.SUBMITTED,"ok"))).place_order(request())
def test_session_error_preserves_cause():
 with pytest.raises(OrderPlacementDependencyError) as caught:runtime(session=FakeSessionManager(error=LookupError("synthetic"))).place_order(request())
 assert isinstance(caught.value.__cause__,LookupError)
def test_deterministic_and_input_immutable():
 value=request();before=value.to_dict();assert runtime().place_order(value)==runtime().place_order(value) and value.to_dict()==before
