from dataclasses import replace
import pytest
from app.execution_orchestrator import PaperTradingCycleOutcome
from app.historical_replay import *
from tests.historical_replay.helpers import Coordinator,event,request,runtime

@pytest.mark.parametrize("outcome,status",[(PaperTradingCycleOutcome.EXECUTED,HistoricalReplayEventStatus.COMPLETED),(PaperTradingCycleOutcome.PARTIALLY_EXECUTED,HistoricalReplayEventStatus.COMPLETED),(PaperTradingCycleOutcome.NO_ACTION,HistoricalReplayEventStatus.COMPLETED),(PaperTradingCycleOutcome.STRATEGY_REJECTED,HistoricalReplayEventStatus.REJECTED),(PaperTradingCycleOutcome.RISK_REJECTED,HistoricalReplayEventStatus.REJECTED),(PaperTradingCycleOutcome.PLANNING_REJECTED,HistoricalReplayEventStatus.REJECTED),(PaperTradingCycleOutcome.EXECUTION_REJECTED,HistoricalReplayEventStatus.REJECTED),(PaperTradingCycleOutcome.DISABLED,HistoricalReplayEventStatus.REJECTED)])
def test_upstream_outcome_grouping(outcome,status):
    base=Coordinator()
    coordinator=Coordinator(callback=lambda req:replace(base.engine.execute(req),outcome=outcome))
    result=runtime(coordinator)[0].replay(request((event(),)))
    assert result.event_results[0].status is status and result.event_results[0].reasons==(outcome.value,)
def test_stop_on_failure_marks_remaining_skipped_and_failed_status():
    c=Coordinator(errors={"orchestrator-0":RuntimeError("boom")});result=runtime(c)[0].replay(request((event(0),event(1))))
    assert tuple(x.status for x in result.event_results)==(HistoricalReplayEventStatus.FAILED,HistoricalReplayEventStatus.SKIPPED)
    assert result.status is HistoricalReplayStatus.FAILED and len(c.calls)==1 and result.final_state is None
    assert result.event_results[0].exception_type=="RuntimeError" and result.event_results[0].failed_stage=="coordinator_execution"
def test_stop_after_prior_success_is_partial_and_preserves_state():
    c=Coordinator(errors={"orchestrator-1":RuntimeError("boom")});result=runtime(c)[0].replay(request((event(0),event(1),event(2))))
    assert result.status is HistoricalReplayStatus.PARTIALLY_COMPLETED and result.final_state==result.event_results[0].resulting_state
    assert result.event_results[2].status is HistoricalReplayEventStatus.SKIPPED and len(c.calls)==2
def test_continue_failure_uses_last_valid_state():
    c=Coordinator(errors={"orchestrator-1":RuntimeError("boom")});result=runtime(c,failure_mode=HistoricalReplayFailureMode.CONTINUE_ON_FAILURE)[0].replay(request((event(0),event(1),event(2))))
    assert tuple(x.status for x in result.event_results)==(HistoricalReplayEventStatus.COMPLETED,HistoricalReplayEventStatus.FAILED,HistoricalReplayEventStatus.COMPLETED)
    assert c.calls[2].paper_account==result.event_results[0].resulting_state and result.final_state==result.event_results[2].resulting_state
def test_invalid_coordinator_result_becomes_failed_no_retry():
    c=Coordinator(callback=lambda req:object());result=runtime(c)[0].replay(request((event(),)))
    assert result.status is HistoricalReplayStatus.FAILED and len(c.calls)==1
    assert result.event_results[0].failed_stage=="result_validation" and result.event_results[0].exception_type=="HistoricalReplayResultValidationError"
