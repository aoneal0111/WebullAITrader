from app.trade_journal import TradeJournalStatus
from app.trading_cycle import TradingCycleStage,TradingCycleStageStatus
from app.trade_journal_batch.exceptions import TradeJournalBatchDependencyError,TradeJournalBatchValidationError
from app.trade_journal_batch.models import TradeJournalBatchRequest
def validate_dependencies(appender,factory):
    if appender is None or not callable(getattr(appender,"append",None)):raise TradeJournalBatchDependencyError("appender must expose append(request)")
    if factory is None or not callable(getattr(factory,"create",None)):raise TradeJournalBatchDependencyError("factory must expose create(...)")
def validate_request(request,minimal=False):
    if not isinstance(request,TradeJournalBatchRequest):raise TradeJournalBatchValidationError("request must be TradeJournalBatchRequest")
    if minimal:return request
    if request.identity.journal_id!=request.initial_journal.journal_id:raise TradeJournalBatchValidationError("journal identity mismatch")
    if request.initial_journal.status is TradeJournalStatus.ARCHIVED:raise TradeJournalBatchValidationError("archived journal cannot accept batch")
    items=request.items;state=request.initial_journal;policy=request.journal_policy
    entry_ids=tuple(x.entry_id for x in items);cycle_ids=tuple(x.cycle.identity.cycle_id for x in items)
    if len(set(entry_ids))!=len(entry_ids) or any(x in {e.entry_id for e in state.entries} for x in entry_ids):raise TradeJournalBatchValidationError("duplicate entry ID")
    if not policy.allow_duplicate_cycle_ids and (len(set(cycle_ids))!=len(cycle_ids) or any(x in {e.cycle_id for e in state.entries} for x in cycle_ids)):raise TradeJournalBatchValidationError("duplicate cycle ID")
    if state.total_entries+len(items)>policy.maximum_entries:raise TradeJournalBatchValidationError("maximum_entries would be exceeded")
    previous=state.entries[-1].recorded_at if state.entries else None
    for item in items:
        if item.recorded_at<item.cycle.timing.completed_at:raise TradeJournalBatchValidationError("recorded_at cannot precede cycle completion")
        if previous is not None and item.recorded_at<previous:raise TradeJournalBatchValidationError("batch recorded_at values must be chronological")
        previous=item.recorded_at
        if policy.require_completed_cycle and item.cycle.stage_records:
            completed=next((x for x in item.cycle.stage_records if x.stage is TradingCycleStage.COMPLETED),None)
            if completed is None or completed.status is not TradingCycleStageStatus.COMPLETED:raise TradeJournalBatchValidationError("cycle must be completed")
    return request
