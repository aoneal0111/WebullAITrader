from app.backtest_suite import *
from tests.backtest_suite.helpers import request,runtime
def test_three_items_sequential_exact_calls_and_continuity():
    req=request(3);engine,runs,reports=runtime();result=engine.run(req)
    assert result.status is BacktestSuiteStatus.COMPLETED and runs.calls==[x.run_request for x in req.items]
    assert len(reports.calls)==3 and all(runs.calls[i] is req.items[i].run_request for i in range(3))
    for i,record in enumerate(result.items):
        assert record.identity is req.items[i].identity and reports.calls[i].run_result is record.run_result and record.report_result is not None
        assert reports.calls[i].identity.report_id==req.items[i].identity.report_id and reports.calls[i].policy is req.items[i].report_policy and reports.calls[i].requested_at is req.items[i].report_requested_at
    assert result.summary==BacktestSuiteSummary(3,3,3,0,0)
def test_child_business_status_does_not_change_coordination_success():
    from dataclasses import replace
    from app.backtest_run import BacktestRunStatus
    base=runtime()[1]
    def outcome(req):return replace(run_runtime_result(req),status=BacktestRunStatus.FAILED,analytics_result=None)
    def run_runtime_result(req):return __import__('tests.backtest_run.helpers',fromlist=['runtime']).runtime()[0].run(req)
    engine,runs,reports=runtime(__import__('tests.backtest_suite.helpers',fromlist=['Runs']).Runs(callback=outcome));result=engine.run(request(1))
    assert result.items[0].status is BacktestSuiteItemStatus.COMPLETED and result.status is BacktestSuiteStatus.COMPLETED
def test_deterministic_equal_runs():
    req=request(2);a=runtime()[0].run(req);b=runtime()[0].run(req);assert a==b and a.to_dict()==b.to_dict()
def test_all_child_run_statuses_are_completed_when_reported():
    from dataclasses import replace
    from app.backtest_run import BacktestRunStatus
    from tests.backtest_suite.helpers import Runs
    for status in BacktestRunStatus:
        def outcome(req,status=status):
            result=__import__('tests.backtest_run.helpers',fromlist=['runtime']).runtime()[0].run(req)
            return replace(result,status=status,analytics_result=None if status is not BacktestRunStatus.COMPLETED else result.analytics_result)
        result=runtime(Runs(callback=outcome))[0].run(request(1))
        assert result.items[0].status is BacktestSuiteItemStatus.COMPLETED and result.status is BacktestSuiteStatus.COMPLETED
