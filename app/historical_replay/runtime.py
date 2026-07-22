from app.execution_orchestrator import PaperTradingCycleOutcome,PaperTradingCycleRequest,PaperTradingCycleResult
from app.historical_replay.exceptions import HistoricalReplayResultValidationError
from app.historical_replay.models import *
from app.historical_replay.validation import validate_dependencies,validate_request

class HistoricalReplayRuntime:
    def __init__(self,coordinator,policy):validate_dependencies(coordinator,policy);self._coordinator=coordinator;self._policy=policy
    def replay(self,request):
        request=validate_request(request,self._policy,minimal=not self._policy.enabled)
        if not self._policy.enabled:
            progress=HistoricalReplayProgress(len(request.events),0,0,0,0,0)
            return HistoricalReplayResult(request.identity,HistoricalReplayStatus.DISABLED,(),progress,request.initial_paper_account,request.started_at,request.completed_at,(HistoricalReplayCriteriaResult("policy_enabled",False,("historical replay disabled",)),),(),(),{"deterministic":True},True)
        request=validate_request(request,self._policy)
        ordered=self._order(request.events)
        if not ordered:
            return HistoricalReplayResult(request.identity,HistoricalReplayStatus.EMPTY,(),HistoricalReplayProgress(0,0,0,0,0,0),request.initial_paper_account,request.started_at,request.completed_at,(HistoricalReplayCriteriaResult("events_present",False,("empty replay accepted",)),),(),(),{"deterministic":True},False)
        results=[];current=request.initial_paper_account;valid=0;stopped=False
        for index,event in enumerate(ordered):
            if stopped:
                results.append(self._event(request,event,HistoricalReplayEventStatus.SKIPPED,None,None,None,("skipped after prior failure",),(),(),None,None));continue
            try:
                metadata={**dict(request.metadata),**dict(event.metadata),"features":dict(event.features),"replay_id":request.identity.replay_id,"event_id":event.event_id,"symbol":event.symbol}
                orchestrator_request=PaperTradingCycleRequest(event.orchestrator_request_id,request.identity.account_id,event.portfolio,current,event.market_price,event.event_time,event.requested_quantity,metadata)
            except Exception as exc:
                results.append(self._failed(request,event,"request_transformation",exc));stopped=self._policy.failure_mode is HistoricalReplayFailureMode.STOP_ON_FAILURE;continue
            try:orchestrator_result=self._coordinator.execute(orchestrator_request)
            except Exception as exc:
                results.append(self._failed(request,event,"coordinator_execution",exc));stopped=self._policy.failure_mode is HistoricalReplayFailureMode.STOP_ON_FAILURE;continue
            try:self._validate_result(request,event,orchestrator_request,orchestrator_result)
            except Exception as exc:
                results.append(self._failed(request,event,"result_validation",exc));stopped=self._policy.failure_mode is HistoricalReplayFailureMode.STOP_ON_FAILURE;continue
            rejected=orchestrator_result.outcome in (PaperTradingCycleOutcome.STRATEGY_REJECTED,PaperTradingCycleOutcome.RISK_REJECTED,PaperTradingCycleOutcome.PLANNING_REJECTED,PaperTradingCycleOutcome.EXECUTION_REJECTED,PaperTradingCycleOutcome.DISABLED)
            status=HistoricalReplayEventStatus.REJECTED if rejected else HistoricalReplayEventStatus.COMPLETED
            current=orchestrator_result.resulting_account;valid+=1
            results.append(self._event(request,event,status,event.orchestrator_request_id,orchestrator_result,current,(orchestrator_result.outcome.value,),(),(),None,None))
        completed=sum(x.status is HistoricalReplayEventStatus.COMPLETED for x in results);rejected=sum(x.status is HistoricalReplayEventStatus.REJECTED for x in results);failed=sum(x.status is HistoricalReplayEventStatus.FAILED for x in results);skipped=sum(x.status is HistoricalReplayEventStatus.SKIPPED for x in results);processed=completed+rejected+failed
        reached=next((x for x in reversed(results) if x.status is not HistoricalReplayEventStatus.SKIPPED),None)
        progress=HistoricalReplayProgress(len(ordered),processed,completed,rejected,skipped,failed,reached.sequence if reached else None,reached.event_id if reached else None)
        status=HistoricalReplayStatus.COMPLETED if failed==0 else HistoricalReplayStatus.PARTIALLY_COMPLETED if valid else HistoricalReplayStatus.FAILED
        criteria=(HistoricalReplayCriteriaResult("policy_enabled",True,()),HistoricalReplayCriteriaResult("events_valid",True,()),HistoricalReplayCriteriaResult("deterministic_order",True,()))
        return HistoricalReplayResult(request.identity,status,tuple(results),progress,current if valid else None,request.started_at,request.completed_at,criteria,(),(),{"deterministic":True,"policy_version":self._policy.version},False)
    def _order(self,events):
        if self._policy.ordering is HistoricalReplayOrdering.INPUT_ORDER:return events
        indexed=tuple(enumerate(events))
        if self._policy.ordering is HistoricalReplayOrdering.EVENT_TIME:return tuple(x[1] for x in sorted(indexed,key=lambda x:(x[1].event_time,x[0])))
        return tuple(x[1] for x in sorted(indexed,key=lambda x:(x[1].event_time,x[1].sequence,x[0])))
    @staticmethod
    def _validate_result(request,event,orchestrator_request,result):
        if not isinstance(result,PaperTradingCycleResult):raise HistoricalReplayResultValidationError("coordinator returned invalid result")
        if result.request_id!=orchestrator_request.request_id or result.account_id!=request.identity.account_id or result.resulting_account.account_id!=request.identity.account_id:raise HistoricalReplayResultValidationError("coordinator result identity mismatch")
        if result.strategy_result and len(result.strategy_result.decisions)==1 and result.strategy_result.decisions[0].symbol!=event.symbol:raise HistoricalReplayResultValidationError("coordinator strategy symbol mismatch")
        if result.risk_result and result.risk_result.requested_quantity!=event.requested_quantity:raise HistoricalReplayResultValidationError("coordinator requested quantity mismatch")
        if result.execution_plan_result and result.execution_plan_result.plan and result.execution_plan_result.plan.instructions[0].symbol!=event.symbol:raise HistoricalReplayResultValidationError("coordinator result symbol mismatch")
    @staticmethod
    def _event(request,event,status,request_id,result,state,reasons,warnings,errors,failed_stage,exception_type):return HistoricalReplayEventResult(request.identity.replay_id,event.event_id,event.sequence,event.symbol,event.event_time,status,request_id,result,state,reasons,warnings,errors,failed_stage,exception_type,event.metadata)
    def _failed(self,request,event,stage,exc):
        warnings=() if not self._policy.include_diagnostics else ();errors=() if not self._policy.include_diagnostics else (f"{stage} failed",)
        return self._event(request,event,HistoricalReplayEventStatus.FAILED,event.orchestrator_request_id,None,None,(),warnings,errors,stage,type(exc).__name__,)
