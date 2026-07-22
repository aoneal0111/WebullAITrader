from dataclasses import replace
from app.backtest_run import *
from tests.backtest_run.helpers import request,runtime
def test_disabled_zero_calls_and_preserves_identity_times():
    req=request(1,enabled=False);engine,stages,factories=runtime();result=engine.run(req)
    assert result.status is BacktestRunStatus.DISABLED and result.identity is req.identity and result.requested_at is req.requested_at
    assert all(not x.calls for x in stages+factories) and all(x.status is BacktestRunStageStatus.SKIPPED for x in result.stage_results)
def test_invalid_nested_identity_zero_calls_rejected():
    req=request(1);bad=replace(req,replay_request=replace(req.replay_request,identity=replace(req.replay_request.identity,run_id="other")))
    engine,stages,factories=runtime();result=engine.run(bad)
    assert result.status is BacktestRunStatus.REJECTED and all(not x.calls for x in stages+factories)
def test_empty_allowed_and_disallowed_stop_after_replay():
    for allow,status in ((True,BacktestRunStatus.EMPTY),(False,BacktestRunStatus.REJECTED)):
        engine,stages,factories=runtime();result=engine.run(request(0,allow_empty=allow))
        assert result.status is status and len(stages[0].calls)==1 and all(not x.calls for x in stages[1:]+factories)
        assert result.replay_result is not None and result.projection_result is None
def test_wrong_dependency_and_request_type():
    import pytest
    with pytest.raises(BacktestRunDependencyError):BacktestRunRuntime(None,None,None,None,None,None,None)
    with pytest.raises(BacktestRunValidationError):runtime()[0].run(object())
