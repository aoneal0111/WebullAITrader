import pytest
from app.market_data import *
from app.session import SessionSnapshot,SessionStatus
from tests.market_data.fixtures import enabled_policy,request
from tests.market_data.helpers import FakeGateway,FakeSessionManager,active_snapshot,quotes
def runtime(session=None,gateway=None,policy=None):return DeterministicMarketDataRuntime(session or FakeSessionManager(),gateway or FakeGateway(),policy or enabled_policy())
def test_construction_no_work():
 s,g=FakeSessionManager(),FakeGateway();runtime(s,g);assert s.calls==0 and not g.requests
def test_success_exactly_one_resolution_gateway_call():
 s,g=FakeSessionManager(),FakeGateway();result=runtime(s,g).get_market_data(request());assert result.success and len(result.quotes)==2 and s.calls==1 and g.requests==[request()]
def test_disabled_no_calls():
 s,g=FakeSessionManager(),FakeGateway();result=runtime(s,g,MarketDataPolicy()).get_market_data(request());assert result.decision is MarketDataDecision.DISABLED and s.calls==0 and not g.requests
@pytest.mark.parametrize("snapshot",[SessionSnapshot(SessionStatus.NO_SESSION,None,(),0),active_snapshot("other")])
def test_invalid_session(snapshot):
 s,g=FakeSessionManager(snapshot),FakeGateway();result=runtime(s,g).get_market_data(request());assert result.decision is MarketDataDecision.SESSION_INVALID and s.calls==1 and not g.requests
def test_gateway_failure_no_retry():
 g=FakeGateway(error=OSError("synthetic"));result=runtime(gateway=g).get_market_data(request());assert result.decision is MarketDataDecision.GATEWAY_FAILURE and len(g.requests)==1
def test_invalid_outputs_and_symbol_order():
 with pytest.raises(MarketDataDependencyError):runtime(session=FakeSessionManager(snapshot="bad")).get_market_data(request())
 with pytest.raises(MarketDataDependencyError):runtime(gateway=FakeGateway(response=[object()])).get_market_data(request())
 with pytest.raises(MarketDataDependencyError):runtime(gateway=FakeGateway(response=tuple(reversed(quotes())))).get_market_data(request())
def test_session_error_cause():
 with pytest.raises(MarketDataDependencyError) as caught:runtime(session=FakeSessionManager(error=LookupError("synthetic"))).get_market_data(request())
 assert isinstance(caught.value.__cause__,LookupError)
def test_deterministic_input_immutable():
 r=request();before=r.to_dict();assert runtime().get_market_data(r)==runtime().get_market_data(r) and r.to_dict()==before
