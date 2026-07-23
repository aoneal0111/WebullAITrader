from dataclasses import replace
from datetime import datetime
import pytest
from app.trade_journal_batch import *
from tests.trade_journal_batch.helpers import request,runtime
def test_disabled_zero_calls_and_preserves_state_and_times():
    req=request(2,batch_policy=TradeJournalBatchPolicy(enabled=False));engine,appender,factory=runtime();result=engine.run(req)
    assert result.status is TradeJournalBatchStatus.DISABLED and result.final_journal is req.initial_journal
    assert result.requested_at is req.requested_at and result.completed_at is req.completed_at and appender.calls==factory.calls==[]
def test_empty_allowed_and_disallowed_zero_calls():
    for allow,status in ((True,TradeJournalBatchStatus.EMPTY),(False,TradeJournalBatchStatus.REJECTED)):
        req=request(items=(),batch_policy=TradeJournalBatchPolicy(allow_empty=allow));engine,appender,factory=runtime();result=engine.run(req)
        assert result.status is status and result.final_journal is req.initial_journal and result.item_results==() and appender.calls==factory.calls==[]
def test_invalid_identity_duplicates_and_chronology_zero_calls():
    req=request(2);cases=(replace(req,identity=replace(req.identity,journal_id="other")),replace(req,items=(req.items[0],replace(req.items[1],entry_id=req.items[0].entry_id))),replace(req,items=(req.items[0],replace(req.items[1],recorded_at=req.items[0].recorded_at.replace(year=2020)))))
    for case in cases:
        engine,appender,factory=runtime();result=engine.run(case)
        assert result.status is TradeJournalBatchStatus.REJECTED and appender.calls==factory.calls==[]
def test_wrong_dependency_and_request_type():
    with pytest.raises(TradeJournalBatchDependencyError):TradeJournalBatchRuntime(None,object())
    engine=runtime()[0]
    with pytest.raises(TradeJournalBatchValidationError):engine.run(object())
