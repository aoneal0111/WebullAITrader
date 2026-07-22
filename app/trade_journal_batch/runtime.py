from app.trade_journal import TradeJournalAppendRequest,TradeJournalAppendResult
from app.trade_journal_batch.exceptions import TradeJournalBatchResultError,TradeJournalBatchValidationError
from app.trade_journal_batch.models import *
from app.trade_journal_batch.validation import validate_dependencies,validate_request
class DeterministicTradeJournalBatchAppendRequestFactory:
    def create(self,batch_request,cycle,current_journal,index):
        item=batch_request.items[index]
        if item.cycle is not cycle:raise TradeJournalBatchResultError("factory cycle identity mismatch")
        return TradeJournalAppendRequest(batch_request.identity.journal_id,item.entry_id,cycle,current_journal,item.recorded_at,item.metadata)
class TradeJournalBatchRuntime:
    def __init__(self,appender,factory):validate_dependencies(appender,factory);self._appender=appender;self._factory=factory
    def run(self,request):
        request=validate_request(request,minimal=True)
        if not request.policy.enabled:return self._result(request,TradeJournalBatchStatus.DISABLED,request.initial_journal,(),True,(TradeJournalBatchCriteriaResult("policy_enabled",False,("batch disabled",)),))
        try:request=validate_request(request)
        except TradeJournalBatchValidationError:return self._result(request,TradeJournalBatchStatus.REJECTED,request.initial_journal,(),False,(TradeJournalBatchCriteriaResult("request_valid",False,("batch validation rejected",)),))
        if not request.items:
            status=TradeJournalBatchStatus.EMPTY if request.policy.allow_empty else TradeJournalBatchStatus.REJECTED
            return self._result(request,status,request.initial_journal,(),False,(TradeJournalBatchCriteriaResult("items_present",False,("empty batch",)),))
        current=request.initial_journal;results=[];successes=0;stopped=False
        for index,item in enumerate(request.items):
            if stopped:
                results.append(TradeJournalBatchItemResult(index,item.cycle.identity.cycle_id,item.entry_id,TradeJournalBatchItemStatus.SKIPPED,None,"Skipped after prior terminal outcome."));continue
            try:
                append_request=self._factory.create(request,item.cycle,current,index)
                if not isinstance(append_request,TradeJournalAppendRequest) or append_request.cycle is not item.cycle or append_request.state is not current:raise TradeJournalBatchResultError("factory returned invalid append request")
                append_result=self._appender.append(append_request)
                self._validate_result(append_request,append_result)
                if append_result.appended:
                    current=append_result.state;successes+=1;results.append(TradeJournalBatchItemResult(index,item.cycle.identity.cycle_id,item.entry_id,TradeJournalBatchItemStatus.COMPLETED,append_result));continue
                results.append(TradeJournalBatchItemResult(index,item.cycle.identity.cycle_id,item.entry_id,TradeJournalBatchItemStatus.REJECTED,append_result,"Trade journal append was rejected."))
                stopped=request.policy.failure_mode is TradeJournalBatchFailureMode.STOP_ON_FAILURE
            except Exception as exc:
                results.append(TradeJournalBatchItemResult(index,item.cycle.identity.cycle_id,item.entry_id,TradeJournalBatchItemStatus.FAILED,None,"Trade journal append failed.",type(exc).__name__))
                stopped=request.policy.failure_mode is TradeJournalBatchFailureMode.STOP_ON_FAILURE
        failed=sum(x.status is TradeJournalBatchItemStatus.FAILED for x in results);rejected=sum(x.status is TradeJournalBatchItemStatus.REJECTED for x in results)
        status=TradeJournalBatchStatus.COMPLETED if failed+rejected==0 else TradeJournalBatchStatus.PARTIALLY_COMPLETED if successes else TradeJournalBatchStatus.FAILED if failed else TradeJournalBatchStatus.REJECTED
        return self._result(request,status,current,tuple(results),False,(TradeJournalBatchCriteriaResult("request_valid",True,()),TradeJournalBatchCriteriaResult("state_continuity",True,())))
    @staticmethod
    def _validate_result(req,result):
        if not isinstance(result,TradeJournalAppendResult):raise TradeJournalBatchResultError("appender returned invalid result")
        if result.state.journal_id!=req.journal_id:raise TradeJournalBatchResultError("append result journal identity mismatch")
        if result.appended:
            if result.disabled or result.entry is None or result.entry.entry_id!=req.entry_id or result.entry.cycle_id!=req.cycle.identity.cycle_id:raise TradeJournalBatchResultError("successful append result identity mismatch")
            if result.state.total_entries!=req.state.total_entries+1 or result.state.entries[:-1]!=req.state.entries or result.state.entries[-1]!=result.entry:raise TradeJournalBatchResultError("successful append state transition is invalid")
        elif result.state!=req.state:raise TradeJournalBatchResultError("rejected append must preserve current state")
    @staticmethod
    def _result(request,status,final,items,disabled,criteria):
        counts={s:sum(x.status is s for x in items) for s in TradeJournalBatchItemStatus}
        progress=TradeJournalBatchProgress(len(request.items),counts[TradeJournalBatchItemStatus.COMPLETED],counts[TradeJournalBatchItemStatus.REJECTED],counts[TradeJournalBatchItemStatus.FAILED],counts[TradeJournalBatchItemStatus.SKIPPED])
        return TradeJournalBatchResult(request.identity,status,request.initial_journal,final,items,progress,request.requested_at,request.completed_at,criteria,(),(),disabled)
