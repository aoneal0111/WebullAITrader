from dataclasses import replace
from datetime import datetime,timedelta
import pytest
from app.trade_journal import *
from tests.trade_journal.helpers import cycle,request,runtime,state

def test_enums():
    assert len(TradeJournalEntryType)==6 and [x.value for x in TradeJournalStatus]==["ACTIVE","ARCHIVED"]
def test_policy_round_trip_and_validation():
    p=TradeJournalPolicy(enabled=True,maximum_entries=5);assert TradeJournalPolicy.from_dict(p.to_dict())==p
    with pytest.raises(TradeJournalValidationError):TradeJournalPolicy(maximum_entries=0)
def test_request_result_state_entry_summary_round_trips():
    req=request();assert TradeJournalAppendRequest.from_dict(req.to_dict())==req
    result=runtime().append(req);assert TradeJournalAppendResult.from_dict(result.to_dict())==result
    assert TradeJournalState.from_dict(result.state.to_dict())==result.state and TradeJournalEntry.from_dict(result.entry.to_dict())==result.entry
def test_invalid_timestamp():
    with pytest.raises(TradeJournalValidationError):request(recorded_at=datetime(2026,1,1))
    with pytest.raises(TradeJournalValidationError):runtime().append(request(recorded_at=cycle().timing.completed_at-timedelta(seconds=1)))
def test_serializer_type_boundary():
    from app.trade_journal import serialize_state
    with pytest.raises(TradeJournalSerializationError):serialize_state({})
def test_dependency_and_input_boundaries():
    with pytest.raises(TradeJournalDependencyError):TradeJournalRuntime(object())
    engine=TradeJournalRuntime(TradeJournalPolicy())
    with pytest.raises(TradeJournalValidationError):engine.append({})
