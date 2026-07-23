from app.backtest_suite import *
from tests.backtest_suite.helpers import Reports,Runs,request,runtime
def test_continue_after_run_failure():
    engine,runs,reports=runtime(Runs(errors={"run-1":ValueError("secret")}));result=engine.run(request(3))
    assert tuple(x.status for x in result.items)==(BacktestSuiteItemStatus.COMPLETED,BacktestSuiteItemStatus.RUN_FAILED,BacktestSuiteItemStatus.COMPLETED)
    assert len(runs.calls)==3 and len(reports.calls)==2 and result.status is BacktestSuiteStatus.PARTIALLY_COMPLETED
    assert result.items[1].message=="Backtest run invocation failed." and result.items[1].error_type=="ValueError"
def test_continue_after_report_failure():
    engine,runs,reports=runtime(reports=Reports(errors={"report-1":RuntimeError("secret")}));result=engine.run(request(3))
    assert tuple(x.status for x in result.items)==(BacktestSuiteItemStatus.COMPLETED,BacktestSuiteItemStatus.REPORT_FAILED,BacktestSuiteItemStatus.COMPLETED)
    assert len(runs.calls)==len(reports.calls)==3 and result.items[1].run_result is not None and result.items[1].report_result is None
def test_no_completed_items_is_failed():
    engine,runs,reports=runtime(Runs(errors={"run-0":RuntimeError(),"run-1":RuntimeError()}));result=engine.run(request(2));assert result.status is BacktestSuiteStatus.FAILED and result.summary.failed_items==2
def test_returned_report_rejected_and_disabled_are_orchestration_rejections():
    from dataclasses import replace
    from app.backtest_report import BacktestReportStatus
    for status in (BacktestReportStatus.REJECTED,BacktestReportStatus.DISABLED):
        base=Reports()
        def outcome(req,status=status):
            value=base.runtime.create(req);return replace(value,status=status,report=None)
        result=runtime(reports=Reports(callback=outcome))[0].run(request(1))
        assert result.items[0].status is BacktestSuiteItemStatus.REPORT_REJECTED and result.items[0].report_result is not None
        assert result.status is BacktestSuiteStatus.FAILED
