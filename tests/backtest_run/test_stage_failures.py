import pytest
from app.backtest_run import *
from tests.backtest_run.helpers import Call,Factory,components,request,runtime
@pytest.mark.parametrize("index,stage",[(0,BacktestRunStage.HISTORICAL_REPLAY),(1,BacktestRunStage.CYCLE_PROJECTION),(2,BacktestRunStage.TRADE_JOURNAL_BATCH),(3,BacktestRunStage.ANALYTICS)])
def test_runtime_exception_stops_at_exact_stage(index,stage):
    stages,factories=components();original=stages[index];stages=list(stages);stages[index]=Call(original.method,error=ValueError("secret"));engine,stages,factories=runtime(tuple(stages),factories);result=engine.run(request(1))
    assert result.stopped_at is stage and result.status in (BacktestRunStatus.FAILED,BacktestRunStatus.PARTIALLY_COMPLETED)
    assert result.error_type=="ValueError" and next(x for x in result.stage_results if x.stage is stage).status is BacktestRunStageStatus.FAILED
    assert all(not x.calls for x in stages[index+1:])
@pytest.mark.parametrize("index,stage",[(0,BacktestRunStage.CYCLE_PROJECTION),(1,BacktestRunStage.TRADE_JOURNAL_BATCH),(2,BacktestRunStage.ANALYTICS)])
def test_factory_exception_stops_before_runtime(index,stage):
    stages,factories=components();factories=list(factories);factories[index]=Factory(factories[index].target,error=RuntimeError("boom"));engine,stages,factories=runtime(stages,tuple(factories));result=engine.run(request(1))
    assert result.stopped_at is stage and result.error_type=="RuntimeError"
    assert not stages[index+1].calls
def test_wrong_stage_result_type_is_normalized():
    stages,factories=components();stages=list(stages);stages[1]=Call("project",callback=lambda req:object());result=runtime(tuple(stages),factories)[0].run(request(1))
    assert result.stopped_at is BacktestRunStage.CYCLE_PROJECTION and result.error_type=="BacktestRunResultError"
def test_analytics_disabled_is_partial_and_preserves_prior_results():
    from app.analytics import AnalyticsResult,AnalyticsStatus,AnalyticsCriteriaResult
    stages,factories=components();stages=list(stages)
    stages[3]=Call("evaluate",callback=lambda req:AnalyticsResult(req.request_id,req.journal.journal_id,AnalyticsStatus.DISABLED,None,(AnalyticsCriteriaResult("enabled",False,("disabled",)),),disabled=True))
    result=runtime(tuple(stages),factories)[0].run(request(1));assert result.status is BacktestRunStatus.PARTIALLY_COMPLETED and result.analytics_result is not None and result.journal_batch_result is not None
