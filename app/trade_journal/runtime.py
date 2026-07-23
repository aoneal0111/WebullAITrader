from decimal import Decimal
from app.trade_journal.exceptions import TradeJournalEvaluationError,TradeJournalValidationError
from app.trade_journal.models import *
from app.trade_journal.validation import validate_dependencies,validate_request
from app.trading_cycle import TradingCycleStage,TradingCycleStageStatus

class DefaultTradeJournalSummaryEvaluator:
    """Aggregates optional amounts by summing contributors; returns None when none contribute."""
    def evaluate(self,state,entry,policy):
        entries=state.entries+(entry,)
        def total(name):
            values=tuple(getattr(x,name) for x in entries if getattr(x,name) is not None)
            return sum(values,Decimal("0")) if values else None
        types={t:sum(x.entry_type is t for x in entries) for t in TradeJournalEntryType}
        return TradeJournalSummary(len(entries),types[TradeJournalEntryType.EXECUTION],types[TradeJournalEntryType.PARTIAL_EXECUTION],types[TradeJournalEntryType.NO_ACTION],types[TradeJournalEntryType.REJECTION],types[TradeJournalEntryType.FAILURE],types[TradeJournalEntryType.DISABLED],total("filled_quantity"),total("fees"),total("realized_profit_loss"),entries[0].recorded_at,entries[-1].recorded_at)

class TradeJournalRuntime:
    def __init__(self,policy,summary_evaluator=None):validate_dependencies(policy,summary_evaluator);self._policy=policy;self._evaluator=summary_evaluator or DefaultTradeJournalSummaryEvaluator()
    def append(self,request):
        request=validate_request(request)
        if not self._policy.enabled:
            return TradeJournalAppendResult(request.state,None,False,True,(TradeJournalCriteriaResult("policy_enabled",False,"trade journal disabled"),),{"deterministic":True})
        self._validate(request)
        entry=self._entry(request)
        try:summary=self._evaluator.evaluate(request.state,entry,self._policy)
        except Exception as exc:raise TradeJournalEvaluationError("trade journal summary evaluator failed") from exc
        if not isinstance(summary,TradeJournalSummary):raise TradeJournalValidationError("summary evaluator returned invalid summary")
        state=TradeJournalState(request.journal_id,request.state.status,request.state.entries+(entry,),request.state.total_entries+1,summary if self._policy.include_summary else None,request.state.metadata)
        criteria=(TradeJournalCriteriaResult("identity_valid",True,"journal and cycle identities validated"),TradeJournalCriteriaResult("entry_appended",True,"one immutable entry appended"))
        return TradeJournalAppendResult(state,entry,True,False,criteria,{"deterministic":True,"policy_version":self._policy.version})
    def _validate(self,r):
        if r.journal_id!=r.state.journal_id:raise TradeJournalValidationError("journal identity mismatch")
        if r.state.status is TradeJournalStatus.ARCHIVED:raise TradeJournalValidationError("archived journal cannot accept entries")
        if any(x.entry_id==r.entry_id for x in r.state.entries):raise TradeJournalValidationError("duplicate entry_id")
        if not self._policy.allow_duplicate_cycle_ids and any(x.cycle_id==r.cycle.identity.cycle_id for x in r.state.entries):raise TradeJournalValidationError("duplicate cycle_id")
        if r.state.total_entries>=self._policy.maximum_entries:raise TradeJournalValidationError("maximum_entries reached; append rejected")
        i=r.cycle.identity
        if not i.cycle_id or not i.request_id or not i.account_id:raise TradeJournalValidationError("malformed cycle identity")
        if r.recorded_at<r.cycle.timing.completed_at:raise TradeJournalValidationError("recorded_at cannot precede cycle completion")
        if r.state.entries and r.recorded_at<r.state.entries[-1].recorded_at:raise TradeJournalValidationError("recorded_at cannot precede last journal entry")
        if self._policy.require_completed_cycle and r.cycle.stage_records:
            completed=next((x for x in r.cycle.stage_records if x.stage is TradingCycleStage.COMPLETED),None)
            if completed is None or completed.status is not TradingCycleStageStatus.COMPLETED:raise TradeJournalValidationError("cycle must be completed")
    def _entry(self,r):
        c=r.cycle;t=c.decision_trace;m=c.metrics;d=c.diagnostics
        mapping={TradingCycleOutcome.EXECUTED:TradeJournalEntryType.EXECUTION,TradingCycleOutcome.PARTIALLY_EXECUTED:TradeJournalEntryType.PARTIAL_EXECUTION,TradingCycleOutcome.NO_ACTION:TradeJournalEntryType.NO_ACTION,TradingCycleOutcome.STRATEGY_REJECTED:TradeJournalEntryType.REJECTION,TradingCycleOutcome.RISK_REJECTED:TradeJournalEntryType.REJECTION,TradingCycleOutcome.PLANNING_REJECTED:TradeJournalEntryType.REJECTION,TradingCycleOutcome.EXECUTION_REJECTED:TradeJournalEntryType.REJECTION,TradingCycleOutcome.FAILED:TradeJournalEntryType.FAILURE,TradingCycleOutcome.DISABLED:TradeJournalEntryType.DISABLED}
        reasons=(() if t is None else t.strategy_reasons+t.risk_reasons)+(d.reason_codes if d else ())
        warnings=d.warnings if d and self._policy.include_diagnostics else ();errors=d.errors if d and self._policy.include_diagnostics else ()
        return TradeJournalEntry(r.entry_id,r.journal_id,c.identity.cycle_id,c.identity.request_id,c.identity.account_id,c.identity.symbol,c.identity.mode,c.outcome,mapping[c.outcome],r.recorded_at,c.timing.started_at,c.timing.completed_at,t.strategy_signal if t else None,t.risk_outcome if t else None,t.planner_decision if t else None,t.execution_outcome if t else None,t.requested_quantity if t else None,t.approved_quantity if t else None,t.planned_quantity if t else None,t.filled_quantity if t else None,t.execution_price if t else None,t.fees if t else None,t.realized_profit_loss if t else None,m.starting_equity if m else None,m.ending_equity if m else None,m.equity_change if m else None,d.rejection_stage if d else None,d.failed_stage if d else None,reasons,warnings,errors,r.metadata)
