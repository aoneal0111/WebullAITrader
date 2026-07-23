from typing import get_type_hints
from dataclasses import replace
from app.broker import BrokerOrderResult
from app.live_trading import *
from app.research_portfolio import ResearchPortfolioStatus
from app.research_portfolio import ResearchPortfolioRequest,ResearchPortfolioResult
from tests.live_trading.helpers import BrokerExecutor,ResearchExecutor,request,valid_broker_result
def test_constructor_accepts_only_injected_structural_dependencies_without_work():
    research=ResearchExecutor();broker=BrokerExecutor();runtime=LiveTradingRuntime(research,broker)
    assert runtime._research_executor is research and runtime._broker_executor is broker
def test_local_research_executor_protocol_matches_portfolio_boundary():
    hints=get_type_hints(ResearchPortfolioExecutor.run)
    assert hints["request"] is ResearchPortfolioRequest and hints["return"] is ResearchPortfolioResult
def test_research_then_brokers_exact_order_and_continuity():
    events=[];req=request(3,enabled=True,fail_fast=False);research=ResearchExecutor(events=events);broker=BrokerExecutor(events=events);result=LiveTradingRuntime(research,broker).run(req)
    assert events==["research","broker-0","broker-1","broker-2"] and research.calls[0] is req.research_request
    assert all(broker.calls[i] is req.orders[i].broker_request for i in range(3))
    assert all(result.orders[i].broker_result is not None and result.orders[i].broker_request is req.orders[i].broker_request for i in range(3))
    assert result.status is LiveTradingStatus.COMPLETED and result.summary==LiveTradingSummary(3,3,3,0,0)
def test_every_valid_research_status_allows_broker_execution():
    req=request(1,enabled=True)
    base=ResearchExecutor().run(req.research_request)
    for status in ResearchPortfolioStatus:
        research=ResearchExecutor(response=replace(base,status=status));broker=BrokerExecutor();result=LiveTradingRuntime(research,broker).run(req)
        assert len(broker.calls)==1 and result.research.status is LiveTradingResearchStatus.COMPLETED
def test_exact_returned_dependency_results_are_preserved():
    req=request(1,enabled=True);research_result=ResearchExecutor().run(req.research_request)
    broker_result=valid_broker_result(req.orders[0].broker_request);assert isinstance(broker_result,BrokerOrderResult)
    result=LiveTradingRuntime(ResearchExecutor(response=research_result),BrokerExecutor(responses=[broker_result])).run(req)
    assert result.research.research_result is research_result and result.orders[0].broker_result is broker_result
