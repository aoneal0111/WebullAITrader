from app.backtest_suite import *
from tests.backtest_suite.helpers import Reports,Runs,request,runtime
def test_fail_fast_run_failure_skips_remaining():
    req=request(3,fail_fast=True);engine,runs,reports=runtime(Runs(errors={"run-1":ValueError()}));result=engine.run(req)
    assert tuple(x.status for x in result.items)==(BacktestSuiteItemStatus.COMPLETED,BacktestSuiteItemStatus.RUN_FAILED,BacktestSuiteItemStatus.SKIPPED)
    assert len(runs.calls)==2 and len(reports.calls)==1 and result.items[2].run_request is req.items[2].run_request and result.items[2].run_result is None
    assert result.status is BacktestSuiteStatus.PARTIALLY_COMPLETED and result.summary.skipped_items==1
def test_fail_fast_report_failure_first_is_failed():
    engine,runs,reports=runtime(reports=Reports(errors={"report-0":RuntimeError()}));result=engine.run(request(3,fail_fast=True))
    assert result.status is BacktestSuiteStatus.FAILED and len(runs.calls)==len(reports.calls)==1
    assert tuple(x.status for x in result.items[1:])==(BacktestSuiteItemStatus.SKIPPED,BacktestSuiteItemStatus.SKIPPED)
