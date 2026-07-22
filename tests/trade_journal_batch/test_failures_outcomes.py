from dataclasses import replace
from app.trade_journal import TradeJournalAppendResult,TradeJournalCriteriaResult
from app.trade_journal_batch import *
from tests.trade_journal_batch.helpers import Appender,Factory,request,runtime
def test_stop_on_middle_failure_skips_rest_and_preserves_state():
    req=request(3);appender=Appender(errors={"entry-1":ValueError("secret")});engine,_,factory=runtime(appender);result=engine.run(req)
    assert result.status is TradeJournalBatchStatus.PARTIALLY_COMPLETED and len(appender.calls)==len(factory.calls)==2
    assert tuple(x.status for x in result.item_results)==(TradeJournalBatchItemStatus.COMPLETED,TradeJournalBatchItemStatus.FAILED,TradeJournalBatchItemStatus.SKIPPED)
    assert result.final_journal is result.item_results[0].append_result.state and result.item_results[1].error_type=="ValueError"
    assert result.item_results[1].message=="Trade journal append failed."
def test_first_failure_is_failed_and_zero_state_change():
    req=request(2);engine,appender,factory=runtime(Appender(errors={"entry-0":RuntimeError("boom")}));result=engine.run(req)
    assert result.status is TradeJournalBatchStatus.FAILED and result.final_journal is req.initial_journal and len(appender.calls)==1
def test_continue_failure_uses_last_valid_state_and_attempts_all():
    req=request(3,batch_policy=TradeJournalBatchPolicy(failure_mode=TradeJournalBatchFailureMode.CONTINUE_ON_FAILURE));appender=Appender(errors={"entry-1":RuntimeError("boom")});engine,_,factory=runtime(appender);result=engine.run(req)
    assert result.status is TradeJournalBatchStatus.PARTIALLY_COMPLETED and len(appender.calls)==len(factory.calls)==3
    assert appender.calls[2].state is result.item_results[0].append_result.state and result.final_journal is result.item_results[2].append_result.state
def test_valid_downstream_disabled_is_rejection_not_exception():
    def disabled(req):return TradeJournalAppendResult(req.state,None,False,True,(TradeJournalCriteriaResult("policy_enabled",False,"disabled"),),{})
    req=request(2);engine,appender,factory=runtime(Appender(callback=disabled));result=engine.run(req)
    assert result.status is TradeJournalBatchStatus.REJECTED and result.item_results[0].status is TradeJournalBatchItemStatus.REJECTED
    assert result.item_results[0].append_result is not None and result.item_results[0].error_type is None and len(appender.calls)==1
def test_invalid_result_and_factory_failure_are_normalized():
    req=request(2)
    for appender,factory in ((Appender(callback=lambda req:object()),Factory()),(Appender(),Factory(errors={0:ValueError("boom")}))):
        engine,_,_=runtime(appender,factory);result=engine.run(req)
        assert result.status is TradeJournalBatchStatus.FAILED and result.final_journal is req.initial_journal and result.item_results[0].status is TradeJournalBatchItemStatus.FAILED
def test_factory_receives_exact_inputs():
    req=request(2);engine,appender,factory=runtime();result=engine.run(req)
    assert factory.calls[0]==(req,req.items[0].cycle,req.initial_journal,0)
    assert factory.calls[1][2] is result.item_results[0].append_result.state
