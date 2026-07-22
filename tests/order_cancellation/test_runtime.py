import pytest
from app.order_cancellation import *
from app.session import SessionSnapshot,SessionStatus
from tests.order_cancellation.fixtures import enabled_policy,request
from tests.order_cancellation.helpers import FakeGateway,FakeSessionManager,acknowledgement,active_snapshot
def runtime(session=None,gateway=None,policy=None):return DeterministicOrderCancellationRuntime(session or FakeSessionManager(),gateway or FakeGateway(),policy or enabled_policy())
def test_construction_no_work():
 s,g=FakeSessionManager(),FakeGateway();runtime(s,g);assert s.calls==0 and not g.requests
def test_success_exactly_once():
 s,g=FakeSessionManager(),FakeGateway();result=runtime(s,g).cancel_order(request());assert result.success and result.acknowledgement_state is CancellationAcknowledgementState.CANCELED and s.calls==1 and g.requests==[request()]
def test_disabled_no_work():
 s,g=FakeSessionManager(),FakeGateway();result=runtime(s,g,OrderCancellationPolicy()).cancel_order(request());assert result.decision is OrderCancellationDecision.DISABLED and s.calls==0 and not g.requests
@pytest.mark.parametrize("snapshot",[SessionSnapshot(SessionStatus.NO_SESSION,None,(),0),active_snapshot("other")])
def test_invalid_session(snapshot):
 s,g=FakeSessionManager(snapshot),FakeGateway();result=runtime(s,g).cancel_order(request());assert result.decision is OrderCancellationDecision.SESSION_INVALID and s.calls==1 and not g.requests
def test_not_found_rejected_and_gateway_failure_without_retry():
 missing=FakeGateway(None);assert runtime(gateway=missing).cancel_order(request()).decision is OrderCancellationDecision.ORDER_NOT_FOUND and len(missing.requests)==1
 rejected=FakeGateway(acknowledgement(False));result=runtime(gateway=rejected).cancel_order(request());assert result.decision is OrderCancellationDecision.CANCELLATION_REJECTED and len(rejected.requests)==1
 failed=FakeGateway(error=OSError("synthetic"));assert runtime(gateway=failed).cancel_order(request()).decision is OrderCancellationDecision.GATEWAY_FAILURE and len(failed.requests)==1
def test_identity_mismatches():
 with pytest.raises(OrderCancellationIdentityError):runtime(gateway=FakeGateway(acknowledgement(broker_order_id="other"))).cancel_order(request())
 with pytest.raises(OrderCancellationIdentityError):runtime(gateway=FakeGateway(acknowledgement(client_order_id="other"))).cancel_order(request())
def test_optional_client_identity():assert runtime().cancel_order(request(None)).success
def test_malformed_dependencies_and_preserved_cause():
 with pytest.raises(OrderCancellationDependencyError):runtime(gateway=FakeGateway("bad")).cancel_order(request())
 with pytest.raises(OrderCancellationDependencyError):runtime(session=FakeSessionManager(snapshot="bad")).cancel_order(request())
 with pytest.raises(OrderCancellationDependencyError) as caught:runtime(session=FakeSessionManager(error=LookupError("synthetic"))).cancel_order(request())
 assert isinstance(caught.value.__cause__,LookupError)
def test_determinism_and_input_immutability():
 value=request();before=value.to_dict();assert runtime().cancel_order(value)==runtime().cancel_order(value) and value.to_dict()==before
