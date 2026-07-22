from dataclasses import replace
from app.replay_cycle_projection import *
from app.trading_cycle import TradingCycleBuildResult
from tests.historical_replay.helpers import event
from tests.replay_cycle_projection.helpers import BuilderSpy,projection,replay
def test_stop_on_failure_preserves_prior_cycle_and_skips_later_eligible():
    source=replay((event(0),event(1),event(2)));builder=BuilderSpy(errors={"cycle-1":ValueError("unsafe")});runtime,_,request=projection(source,builder)
    result=runtime.project(request)
    assert result.status is ReplayCycleProjectionStatus.PARTIALLY_COMPLETED and len(builder.calls)==2 and len(result.cycles)==1
    assert tuple(x.status for x in result.item_results)==(ReplayCycleProjectionItemStatus.COMPLETED,ReplayCycleProjectionItemStatus.FAILED,ReplayCycleProjectionItemStatus.SKIPPED)
    assert result.item_results[1].exception_type=="ValueError" and result.item_results[1].errors==("trading cycle projection failed",)
def test_continue_on_failure_projects_later_event():
    source=replay((event(0),event(1),event(2)));builder=BuilderSpy(errors={"cycle-1":RuntimeError("boom")});runtime,_,request=projection(source,builder,failure_mode=ReplayCycleProjectionFailureMode.CONTINUE_ON_FAILURE)
    result=runtime.project(request)
    assert result.status is ReplayCycleProjectionStatus.PARTIALLY_COMPLETED and len(builder.calls)==3
    assert tuple(x.identity.cycle_id for x in result.cycles)==("cycle-0","cycle-2")
def test_all_eligible_fail_is_failed():
    source=replay();builder=BuilderSpy(errors={"cycle-0":RuntimeError("boom")});runtime,_,request=projection(source,builder);result=runtime.project(request)
    assert result.status is ReplayCycleProjectionStatus.FAILED and result.cycles==()
def test_invalid_builder_output_is_safe_failed_item():
    source=replay();builder=BuilderSpy(callback=lambda req:object());runtime,_,request=projection(source,builder);result=runtime.project(request)
    assert result.status is ReplayCycleProjectionStatus.FAILED and result.item_results[0].exception_type=="ReplayCycleProjectionResultError"
    assert not any(isinstance(value,BaseException) for value in result.item_results[0].__reduce__()[1])
def test_identity_mismatched_builder_output_is_rejected():
    source=replay();base=BuilderSpy()
    def bad(req):
        built=base.builder.build(req);cycle=replace(built.cycle,identity=replace(built.cycle.identity,cycle_id="wrong"));return TradingCycleBuildResult(cycle,built.criteria_results,built.metadata)
    runtime,builder,request=projection(source,BuilderSpy(callback=bad));result=runtime.project(request)
    assert result.status is ReplayCycleProjectionStatus.FAILED and result.item_results[0].exception_type=="ReplayCycleProjectionResultError"
