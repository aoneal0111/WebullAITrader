from dataclasses import replace
import pytest
from app.live_trading import *
from tests.live_trading.helpers import BrokerExecutor,ResearchExecutor,request
def test_structural_validation_and_duplicate_order_are_deterministic():
    req=request(2);duplicate=replace(req,orders=(req.orders[0],replace(req.orders[1],identity=req.orders[0].identity)))
    assert validate_request(req)==()
    assert validate_request(duplicate)==("duplicate order entry ID at order entry order-entry-0",)
def test_wrong_request_and_dependencies_are_rejected_without_calls():
    with pytest.raises(LiveTradingValidationError):validate_request(object())
    with pytest.raises(LiveTradingDependencyError):LiveTradingRuntime(None,BrokerExecutor())
    with pytest.raises(LiveTradingDependencyError):LiveTradingRuntime(ResearchExecutor(),None)
    with pytest.raises(LiveTradingDependencyError):LiveTradingRuntime(ResearchExecutor,BrokerExecutor())
def test_disabled_makes_zero_dependency_calls():
    research=ResearchExecutor();broker=BrokerExecutor();result=LiveTradingRuntime(research,broker).run(request(2,enabled=False))
    assert result.status is LiveTradingStatus.DISABLED and research.calls==broker.calls==[] and result.summary==LiveTradingSummary(0,0,0,0,0)
def test_duplicate_rejection_makes_zero_dependency_calls():
    req=request(2,enabled=True);duplicate=replace(req,orders=(req.orders[0],replace(req.orders[1],identity=req.orders[0].identity)))
    research=ResearchExecutor();broker=BrokerExecutor();result=LiveTradingRuntime(research,broker).run(duplicate)
    assert result.status is LiveTradingStatus.REJECTED and research.calls==broker.calls==[] and result.orders==()
def test_enabled_empty_invokes_research_then_returns_empty():
    research=ResearchExecutor();broker=BrokerExecutor();req=request(0,enabled=True);result=LiveTradingRuntime(research,broker).run(req)
    assert result.status is LiveTradingStatus.EMPTY and research.calls==[req.research_request] and broker.calls==[]
