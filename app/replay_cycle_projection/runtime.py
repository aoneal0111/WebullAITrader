from app.trading_cycle import TradingCycleBuildRequest,TradingCycleBuildResult,TradingCycleOutcome
from app.replay_cycle_projection.exceptions import ReplayCycleProjectionResultError
from app.replay_cycle_projection.models import *
from app.replay_cycle_projection.validation import eligibility,validate_dependencies,validate_request
class ReplayCycleProjectionRuntime:
    """Projects eligible replay records through an injected Trading Cycle boundary."""
    def __init__(self,builder,policy):validate_dependencies(builder,policy);self._builder=builder;self._policy=policy
    def project(self,request):
        request=validate_request(request,minimal=not self._policy.enabled);replay=request.replay_result
        if not self._policy.enabled:return self._result(request,ReplayCycleProjectionStatus.DISABLED,(),(),True,(ReplayCycleProjectionCriteriaResult("policy_enabled",False,("projection disabled",)),))
        request=validate_request(request);eligible=sum(eligibility(x) is not None for x in replay.event_results)
        if eligible==0:
            status=ReplayCycleProjectionStatus.EMPTY if self._policy.allow_empty else ReplayCycleProjectionStatus.REJECTED
            items=tuple(self._ineligible(replay,x) for x in replay.event_results)
            return self._result(request,status,items,(),False,(ReplayCycleProjectionCriteriaResult("eligible_events",False,("no eligible replay events",)),))
        items=[];cycles=[];stopped=False
        for source in replay.event_results:
            kind=eligibility(source)
            if kind is None:items.append(self._ineligible(replay,source));continue
            if stopped:items.append(self._item(replay,source,ReplayCycleProjectionItemStatus.SKIPPED,None,("skipped after projection failure",),(),None,None));continue
            try:
                p=source.cycle_provenance
                build_request=TradingCycleBuildRequest(p.cycle_id,source.orchestrator_request_id,replay.identity.account_id,p.mode,p.started_at,p.completed_at,p.portfolio_before,None,p.original_account,source.resulting_state,orchestrator_result=source.orchestrator_result,execution_timestamp=source.event_time,metadata=p.metadata)
                built=self._builder.build(build_request)
                self._validate_output(source,built)
                cycle=built.cycle;cycles.append(cycle)
                item_status=ReplayCycleProjectionItemStatus.REJECTED if kind=="rejected" else ReplayCycleProjectionItemStatus.COMPLETED
                items.append(self._item(replay,source,item_status,cycle,(source.orchestrator_result.outcome.value,),(),None,None))
            except Exception as exc:
                errors=("trading cycle projection failed",) if self._policy.include_diagnostics else ()
                items.append(self._item(replay,source,ReplayCycleProjectionItemStatus.FAILED,None,(),errors,"TRADING_CYCLE",type(exc).__name__))
                stopped=self._policy.failure_mode is ReplayCycleProjectionFailureMode.STOP_ON_FAILURE
        failed=sum(x.status is ReplayCycleProjectionItemStatus.FAILED for x in items)
        status=ReplayCycleProjectionStatus.COMPLETED if failed==0 else ReplayCycleProjectionStatus.PARTIALLY_COMPLETED if cycles else ReplayCycleProjectionStatus.FAILED
        criteria=(ReplayCycleProjectionCriteriaResult("eligible_events",True,()),ReplayCycleProjectionCriteriaResult("identity_continuity",True,()))
        return self._result(request,status,tuple(items),tuple(cycles),False,criteria)
    @staticmethod
    def _validate_output(source,built):
        if not isinstance(built,TradingCycleBuildResult):raise ReplayCycleProjectionResultError("builder returned invalid result")
        cycle=built.cycle;p=source.cycle_provenance;o=source.orchestrator_result
        if cycle.identity.cycle_id!=p.cycle_id or cycle.identity.request_id!=source.orchestrator_request_id or cycle.identity.account_id!=o.account_id or cycle.identity.mode is not p.mode:raise ReplayCycleProjectionResultError("trading cycle identity mismatch")
        if cycle.timing.started_at!=p.started_at or cycle.timing.completed_at!=p.completed_at or cycle.outcome is not TradingCycleOutcome(o.outcome.value):raise ReplayCycleProjectionResultError("trading cycle timing or outcome mismatch")
        if cycle.portfolio_before!=p.portfolio_before or cycle.original_account!=p.original_account or cycle.resulting_account!=source.resulting_state:raise ReplayCycleProjectionResultError("trading cycle state mismatch")
    @staticmethod
    def _item(replay,source,status,cycle,reasons,errors,stage,exception):return ReplayCycleProjectionItemResult(replay.identity.replay_id,source.event_id,source.sequence,source.cycle_provenance.cycle_id,status,cycle,reasons,errors,stage,exception,source.metadata)
    def _ineligible(self,replay,source):return self._item(replay,source,ReplayCycleProjectionItemStatus.INELIGIBLE,None,(source.status.value,),(),None,None)
    @staticmethod
    def _result(request,status,items,cycles,disabled,criteria):
        counts={s:sum(x.status is s for x in items) for s in ReplayCycleProjectionItemStatus};eligible=counts[ReplayCycleProjectionItemStatus.COMPLETED]+counts[ReplayCycleProjectionItemStatus.REJECTED]+counts[ReplayCycleProjectionItemStatus.FAILED]+counts[ReplayCycleProjectionItemStatus.SKIPPED]
        progress=ReplayCycleProjectionProgress(len(items),eligible,counts[ReplayCycleProjectionItemStatus.COMPLETED],counts[ReplayCycleProjectionItemStatus.REJECTED],counts[ReplayCycleProjectionItemStatus.INELIGIBLE],counts[ReplayCycleProjectionItemStatus.FAILED],counts[ReplayCycleProjectionItemStatus.SKIPPED])
        return ReplayCycleProjectionResult(request.replay_result,status,items,cycles,progress,criteria,(),(),request.metadata,disabled)
