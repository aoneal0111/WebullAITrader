"""Deterministic synchronous Live Trading orchestration."""
from app.broker import BrokerOrderResult
from app.research_portfolio import ResearchPortfolioResult
from app.live_trading.models import *
from app.live_trading.validation import validate_dependencies,validate_request
class LiveTradingRuntime:
    def __init__(self,research_executor,broker_executor):
        validate_dependencies(research_executor,broker_executor);self._research_executor=research_executor;self._broker_executor=broker_executor
    def run(self,request):
        request=validate_request(request,minimal=True)
        if not request.policy.enabled:return self._result(request,LiveTradingStatus.DISABLED,None,(),True,())
        errors=validate_request(request)
        if errors:return self._result(request,LiveTradingStatus.REJECTED,None,(),False,errors)
        try:research_result=self._research_executor.run(request.research_request)
        except Exception as exc:
            research=LiveTradingResearchRecord(LiveTradingResearchStatus.RESEARCH_FAILED,request.research_request,None,type(exc).__name__,"Research portfolio invocation failed.")
            return self._result(request,LiveTradingStatus.FAILED,research,(),True,())
        if not isinstance(research_result,ResearchPortfolioResult):
            research=LiveTradingResearchRecord(LiveTradingResearchStatus.RESEARCH_FAILED,request.research_request,None,"InvalidResearchPortfolioResult","Research portfolio returned an invalid result.")
            return self._result(request,LiveTradingStatus.FAILED,research,(),True,())
        research=LiveTradingResearchRecord(LiveTradingResearchStatus.COMPLETED,request.research_request,research_result,None,None)
        if not request.orders:return self._result(request,LiveTradingStatus.EMPTY,research,(),True,())
        records=[];stopped=False
        for index,order in enumerate(request.orders):
            if stopped:
                records.append(LiveTradingOrderRecord(index,order.identity,LiveTradingOrderStatus.SKIPPED,order.broker_request,None,None,"Skipped because fail-fast policy stopped live trading."));continue
            try:broker_result=self._broker_executor.place_order(order.broker_request)
            except Exception as exc:
                records.append(LiveTradingOrderRecord(index,order.identity,LiveTradingOrderStatus.ORDER_FAILED,order.broker_request,None,type(exc).__name__,"Broker order invocation failed."));stopped=request.policy.fail_fast;continue
            if not isinstance(broker_result,BrokerOrderResult):
                records.append(LiveTradingOrderRecord(index,order.identity,LiveTradingOrderStatus.ORDER_FAILED,order.broker_request,None,"InvalidBrokerOrderResult","Broker order returned an invalid result."));stopped=request.policy.fail_fast;continue
            records.append(LiveTradingOrderRecord(index,order.identity,LiveTradingOrderStatus.COMPLETED,order.broker_request,broker_result,None,None))
        records=tuple(records)
        return self._result(request,self._status(records),research,records,True,())
    @staticmethod
    def _status(records):
        completed=sum(x.status is LiveTradingOrderStatus.COMPLETED for x in records)
        if completed==len(records):return LiveTradingStatus.COMPLETED
        if completed:return LiveTradingStatus.PARTIALLY_COMPLETED
        return LiveTradingStatus.FAILED
    @staticmethod
    def _result(request,status,research,records,accepted,errors):
        completed=sum(x.status is LiveTradingOrderStatus.COMPLETED for x in records);skipped=sum(x.status is LiveTradingOrderStatus.SKIPPED for x in records);failed=len(records)-completed-skipped
        total=len(records) if records else 0
        summary=LiveTradingSummary(total,completed+failed,completed,failed,skipped)
        return LiveTradingResult(request.identity,status,request.requested_at,request.completed_at,research,records,summary,LiveTradingCriteriaResult(accepted,tuple(errors)),tuple(errors),None)
