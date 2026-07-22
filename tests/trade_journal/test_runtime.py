from dataclasses import FrozenInstanceError,replace
from decimal import Decimal
import pytest
from app.trade_journal import *
from app.trading_cycle import TradingCycleDiagnostics,TradingCycleOutcome,TradingCycleStage
from tests.trade_journal.helpers import Evaluator,cycle,request,runtime,state

@pytest.mark.parametrize("outcome,kind",[(TradingCycleOutcome.EXECUTED,TradeJournalEntryType.EXECUTION),(TradingCycleOutcome.PARTIALLY_EXECUTED,TradeJournalEntryType.PARTIAL_EXECUTION),(TradingCycleOutcome.NO_ACTION,TradeJournalEntryType.NO_ACTION),(TradingCycleOutcome.STRATEGY_REJECTED,TradeJournalEntryType.REJECTION),(TradingCycleOutcome.FAILED,TradeJournalEntryType.FAILURE),(TradingCycleOutcome.DISABLED,TradeJournalEntryType.DISABLED)])
def test_entry_type_mapping(outcome,kind):
    c=replace(cycle(),outcome=outcome);result=runtime().append(request(c=c))
    assert result.entry.entry_type is kind and result.entry.cycle_outcome is outcome

def test_append_returns_new_state_and_preserves_original():
    original=state();result=runtime().append(request(s=original))
    assert original.entries==() and result.state is not original and result.state.entries==(result.entry,) and result.state.total_entries==1

def test_entry_copies_cycle_facts():
    c=cycle();entry=runtime().append(request(c=c)).entry
    assert (entry.cycle_id,entry.request_id,entry.account_id,entry.symbol)==("cycle-record-1","cycle-1","acct","AAPL")
    assert entry.strategy_signal=="BUY" and entry.risk_outcome=="APPROVED" and entry.planner_decision=="PLANNED"
    assert entry.filled_quantity==10 and entry.execution_price==100 and entry.fees==0 and entry.starting_equity==10000

def test_partial_cycle_entry():assert runtime().append(request(c=cycle(partial=True))).entry.filled_quantity==5

def test_entry_ordering_across_appends():
    first=runtime().append(request()).state
    second_cycle=replace(cycle(),identity=replace(cycle().identity,cycle_id="cycle-2"))
    second=runtime().append(request(c=second_cycle,s=first,entry_id="entry-2",recorded_at=request().recorded_at)).state
    assert tuple(x.entry_id for x in second.entries)==("entry-1","entry-2")

@pytest.mark.parametrize("duplicate",["entry","cycle"])
def test_duplicate_rejection_is_zero_evaluator_call(duplicate):
    first=runtime().append(request()).state;evaluator=Evaluator(TradeJournalSummary(0,0,0,0,0,0,0,None,None,None,None,None))
    c=cycle();entry_id="entry-1"
    if duplicate=="entry":c=replace(c,identity=replace(c.identity,cycle_id="different"))
    else:entry_id="different"
    with pytest.raises(TradeJournalValidationError):runtime(evaluator).append(request(c=c,s=first,entry_id=entry_id))
    assert evaluator.calls==[]

def test_duplicate_cycle_allowed_by_policy():
    first=runtime().append(request()).state
    result=runtime(allow_duplicate_cycle_ids=True).append(request(s=first,entry_id="entry-2"))
    assert result.state.total_entries==2

def test_journal_mismatch_and_archived_rejected():
    with pytest.raises(TradeJournalValidationError):runtime().append(request(journal_id="wrong"))
    with pytest.raises(TradeJournalValidationError):runtime().append(request(s=state(status=TradeJournalStatus.ARCHIVED)))

def test_maximum_entries_rejects_without_dropping_oldest():
    first=runtime().append(request()).state;c=replace(cycle(),identity=replace(cycle().identity,cycle_id="cycle-2"))
    with pytest.raises(TradeJournalValidationError):runtime(maximum_entries=1).append(request(c=c,s=first,entry_id="entry-2"))
    assert first.total_entries==1

def test_disabled_returns_original_and_zero_calls():
    evaluator=Evaluator();original=state();result=TradeJournalRuntime(TradeJournalPolicy(),evaluator).append(request(s=original))
    assert result.disabled and not result.appended and result.state is original and evaluator.calls==[]

def test_evaluator_exactly_once_and_exception_cause():
    summary=TradeJournalSummary(1,1,0,0,0,0,0,"10","1","2",request().recorded_at,request().recorded_at);e=Evaluator(summary)
    assert runtime(e).append(request()).state.summary is summary and len(e.calls)==1
    bad=Evaluator(error=KeyError("raw"))
    with pytest.raises(TradeJournalEvaluationError) as caught:runtime(bad).append(request())
    assert isinstance(caught.value.__cause__,KeyError) and len(bad.calls)==1

def test_summary_exact_decimal_aggregation_and_timestamps():
    first=runtime().append(request()).state;c=replace(cycle(partial=True),identity=replace(cycle(partial=True).identity,cycle_id="cycle-2"))
    second=runtime().append(request(c=c,s=first,entry_id="entry-2")).state.summary
    assert second.total_cycles==2 and second.executed_cycles==1 and second.partial_cycles==1
    assert second.total_filled_quantity==15 and second.total_fees==0 and second.first_recorded_at==second.last_recorded_at

def test_optional_aggregates_remain_none_without_contributors():
    c=replace(cycle(),decision_trace=None,metrics=None,outcome=TradingCycleOutcome.NO_ACTION)
    summary=runtime().append(request(c=c)).state.summary
    assert summary.total_filled_quantity is None and summary.total_fees is None and summary.total_realized_profit_loss is None

def test_deterministic_and_immutable():
    req=request();a=runtime().append(req);b=runtime().append(req);assert a==b and a.to_dict()==b.to_dict()
    with pytest.raises(FrozenInstanceError):a.state.total_entries=9
