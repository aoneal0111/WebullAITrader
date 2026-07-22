import pytest
from app.positions import *
from app.session import SessionSnapshot,SessionStatus
from tests.positions.fixtures import enabled_policy,request
from tests.positions.helpers import FakeGateway,FakeSessionManager,active_snapshot
def runtime(session=None,gateway=None,policy=None):return DeterministicPositionsRuntime(session or FakeSessionManager(),gateway or FakeGateway(),policy or enabled_policy())
def test_construction_no_work():
 s,g=FakeSessionManager(),FakeGateway();runtime(s,g);assert s.calls==0 and not g.requests
def test_success_exactly_one_resolution_and_gateway_call():
 s,g=FakeSessionManager(),FakeGateway();result=runtime(s,g).get_positions(request());assert result.success and len(result.positions)==2 and s.calls==1 and g.requests==[request()]
def test_disabled_no_collaborator_calls():
 s,g=FakeSessionManager(),FakeGateway();result=runtime(s,g,PositionsPolicy()).get_positions(request());assert result.decision is PositionsDecision.DISABLED and s.calls==0 and not g.requests
@pytest.mark.parametrize("snapshot",[SessionSnapshot(SessionStatus.NO_SESSION,None,(),0),active_snapshot("other")])
def test_invalid_session(snapshot):
 s,g=FakeSessionManager(snapshot),FakeGateway();result=runtime(s,g).get_positions(request());assert result.decision is PositionsDecision.SESSION_INVALID and s.calls==1 and not g.requests
def test_gateway_failure_no_retry():
 g=FakeGateway(error=OSError("synthetic"));result=runtime(gateway=g).get_positions(request());assert result.decision is PositionsDecision.GATEWAY_FAILURE and len(g.requests)==1
def test_invalid_dependency_outputs():
 with pytest.raises(PositionsDependencyError):runtime(session=FakeSessionManager(snapshot="bad")).get_positions(request())
 with pytest.raises(PositionsDependencyError):runtime(gateway=FakeGateway(response=[object()])).get_positions(request())
def test_session_error_preserves_cause():
 with pytest.raises(PositionsDependencyError) as caught:runtime(session=FakeSessionManager(error=LookupError("synthetic"))).get_positions(request())
 assert isinstance(caught.value.__cause__,LookupError)
def test_equivalent_execution_and_input_immutability():
 r=request();before=r.to_dict();assert runtime().get_positions(r)==runtime().get_positions(r) and r.to_dict()==before
