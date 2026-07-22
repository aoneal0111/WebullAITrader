from dataclasses import replace
from app.execution_orchestrator import PaperTradingCycleOutcome
from app.historical_replay import HistoricalReplayEventStatus
from app.replay_cycle_projection import *
from app.trading_cycle import TradingCycleMode,TradingCycleOutcome
from tests.historical_replay.helpers import Coordinator,event
from tests.replay_cycle_projection.helpers import BuilderSpy,projection,replay
def test_successful_multi_event_projection_exact_calls_and_order():
    source=replay((event(0),event(1)));runtime,builder,request=projection(source);result=runtime.project(request)
    assert result.status is ReplayCycleProjectionStatus.COMPLETED and len(builder.calls)==2
    assert tuple(x.identity.cycle_id for x in result.cycles)==("cycle-0","cycle-1")
    assert tuple(x.event_id for x in result.item_results)==("event-0","event-1")
    first=builder.calls[0];p=source.event_results[0].cycle_provenance
    assert first.cycle_id==p.cycle_id and first.mode is p.mode and first.started_at is p.started_at and first.completed_at is p.completed_at
    assert first.portfolio_before is p.portfolio_before and first.original_account is p.original_account
def test_no_action_is_completed_cycle():
    base=Coordinator();coordinator=Coordinator(callback=lambda req:replace(base.engine.execute(req),outcome=PaperTradingCycleOutcome.NO_ACTION,paper_execution_result=None))
    source=replay((event(),),coordinator);result=projection(source)[0].project(projection(source)[2])
    assert result.item_results[0].status is ReplayCycleProjectionItemStatus.COMPLETED and result.cycles[0].outcome is TradingCycleOutcome.NO_ACTION
def test_valid_rejection_projects_a_rejection_cycle():
    base=Coordinator();coordinator=Coordinator(callback=lambda req:replace(base.engine.execute(req),outcome=PaperTradingCycleOutcome.RISK_REJECTED,paper_execution_result=None,execution_plan_result=None))
    source=replay((event(),),coordinator);runtime,builder,request=projection(source);result=runtime.project(request)
    assert result.item_results[0].status is ReplayCycleProjectionItemStatus.REJECTED and len(builder.calls)==1
    assert result.cycles[0].outcome is TradingCycleOutcome.RISK_REJECTED
def test_failed_and_skipped_replay_items_are_ineligible_zero_calls():
    c=Coordinator(errors={"orchestrator-0":RuntimeError("boom")});source=replay((event(0),event(1)),c)
    runtime,builder,request=projection(source,allow_empty=True);result=runtime.project(request)
    assert tuple(x.status for x in result.item_results)==(ReplayCycleProjectionItemStatus.INELIGIBLE,ReplayCycleProjectionItemStatus.INELIGIBLE)
    assert result.status is ReplayCycleProjectionStatus.EMPTY and builder.calls==[]
def test_deterministic_repeated_projection_and_statelessness():
    source=replay((event(0),event(1)));a=projection(source)[0].project(projection(source)[2]);b=projection(source)[0].project(projection(source)[2])
    assert a==b and a.to_dict()==b.to_dict()
