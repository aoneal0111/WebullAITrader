from app.analytics import AnalyticsStatus
from app.backtest_run import *
from tests.backtest_run.helpers import request,runtime
def test_full_success_exact_order_calls_and_object_continuity():
    log=[];engine,stages,factories=runtime(log=log);result=engine.run(request(2))
    assert log==["replay","projection_factory","projection","journal_factory","journal","analytics_factory","analytics"]
    assert all(len(x.calls)==1 for x in stages+factories) and result.status is BacktestRunStatus.COMPLETED and result.stopped_at is BacktestRunStage.COMPLETED
    assert factories[0].calls[0][1] is result.replay_result and factories[1].calls[0][1] is result.projection_result and factories[2].calls[0][1] is result.journal_batch_result
    assert result.analytics_result.status in (AnalyticsStatus.COMPLETED,AnalyticsStatus.INSUFFICIENT_DATA)
def test_exact_downstream_results_preserved():
    engine,stages,factories=runtime();result=engine.run(request(1))
    assert result.replay_result is not None and result.projection_result is not None and result.journal_batch_result is not None and result.analytics_result is not None
def test_repeated_runs_are_deterministic_and_stateless():
    req=request(1);a=runtime()[0].run(req);b=runtime()[0].run(req);assert a==b and a.to_dict()==b.to_dict()
