from app.live_trading import *
from tests.live_trading.helpers import BrokerExecutor,ResearchExecutor,request
def test_initial_research_failure_record_is_safe_and_immutable_contract():
    req=request(0);record=LiveTradingResearchRecord(LiveTradingResearchStatus.RESEARCH_FAILED,req.research_request,None,"RuntimeError","Research portfolio invocation failed.")
    assert record.research_request is req.research_request and record.research_result is None
def test_research_exception_stops_broker_and_is_sanitized():
    req=request(2,enabled=True);research=ResearchExecutor(error=ValueError("secret"));broker=BrokerExecutor();result=LiveTradingRuntime(research,broker).run(req)
    assert result.status is LiveTradingStatus.FAILED and broker.calls==[]
    assert result.research.error_type=="ValueError" and result.research.message=="Research portfolio invocation failed." and "secret" not in str(result.to_dict())
def test_invalid_research_result_stops_broker():
    req=request(1,enabled=True);broker=BrokerExecutor();result=LiveTradingRuntime(ResearchExecutor(response=object()),broker).run(req)
    assert result.research.status is LiveTradingResearchStatus.RESEARCH_FAILED and result.research.error_type=="InvalidResearchPortfolioResult"
    assert result.research.message=="Research portfolio returned an invalid result." and broker.calls==[]
