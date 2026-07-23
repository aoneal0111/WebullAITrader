from app.live_trading import *
from tests.live_trading.helpers import BrokerExecutor,ResearchExecutor,request
def test_initial_skipped_record_contract_preserves_exact_input():
    req=request(1);record=LiveTradingOrderRecord(0,req.orders[0].identity,LiveTradingOrderStatus.SKIPPED,req.orders[0].broker_request,None,None,"Skipped because fail-fast policy stopped live trading.")
    assert record.index==0 and record.identity is req.orders[0].identity and record.broker_request is req.orders[0].broker_request
def test_fail_fast_stops_calls_and_emits_exact_skips():
    req=request(3,enabled=True,fail_fast=True);broker=BrokerExecutor(errors={1:RuntimeError()});result=LiveTradingRuntime(ResearchExecutor(),broker).run(req)
    assert len(broker.calls)==2 and tuple(x.status for x in result.orders)==(LiveTradingOrderStatus.COMPLETED,LiveTradingOrderStatus.ORDER_FAILED,LiveTradingOrderStatus.SKIPPED)
    skipped=result.orders[2];assert skipped.index==2 and skipped.identity is req.orders[2].identity and skipped.broker_request is req.orders[2].broker_request
    assert skipped.broker_result is None and skipped.error_type is None and skipped.message=="Skipped because fail-fast policy stopped live trading."
    assert result.status is LiveTradingStatus.PARTIALLY_COMPLETED and result.summary==LiveTradingSummary(3,2,1,1,1)
