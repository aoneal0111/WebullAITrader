from app.live_trading import *
from tests.live_trading.helpers import BrokerExecutor,ResearchExecutor,request
def test_initial_order_failure_record_preserves_caller_request():
    req=request(1);record=LiveTradingOrderRecord(0,req.orders[0].identity,LiveTradingOrderStatus.ORDER_FAILED,req.orders[0].broker_request,None,"RuntimeError","Broker order invocation failed.")
    assert record.identity is req.orders[0].identity and record.broker_request is req.orders[0].broker_request and record.broker_result is None
def test_continue_after_broker_exception_is_ordered_and_safe():
    req=request(3,enabled=True,fail_fast=False);broker=BrokerExecutor(errors={1:ValueError("secret")});result=LiveTradingRuntime(ResearchExecutor(),broker).run(req)
    assert len(broker.calls)==3 and tuple(x.status for x in result.orders)==(LiveTradingOrderStatus.COMPLETED,LiveTradingOrderStatus.ORDER_FAILED,LiveTradingOrderStatus.COMPLETED)
    assert result.status is LiveTradingStatus.PARTIALLY_COMPLETED and result.summary==LiveTradingSummary(3,3,2,1,0)
    assert result.orders[1].error_type=="ValueError" and result.orders[1].message=="Broker order invocation failed." and "secret" not in str(result.to_dict())
def test_invalid_broker_returns_are_failed():
    req=request(2,enabled=True,fail_fast=False);result=LiveTradingRuntime(ResearchExecutor(),BrokerExecutor(callback=lambda ignored:object())).run(req)
    assert result.status is LiveTradingStatus.FAILED and result.summary==LiveTradingSummary(2,2,0,2,0)
    assert all(x.error_type=="InvalidBrokerOrderResult" and x.message=="Broker order returned an invalid result." for x in result.orders)
