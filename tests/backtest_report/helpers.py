from dataclasses import replace
from app.backtest_report import *
from app.backtest_run import BacktestRunStatus
from tests.backtest_run.helpers import request as run_request,runtime as run_runtime
def source(count=2):return run_runtime()[0].run(run_request(count))
def request(run=None,policy=None):
    run=run or source();return BacktestReportRequest(BacktestReportIdentity("report-1",run.identity.run_id),run,policy or BacktestReportPolicy(),run.completed_at)
def with_status(status):
    run=source(1)
    if status is BacktestRunStatus.COMPLETED:return run
    return replace(run,status=status,analytics_result=None if status is not BacktestRunStatus.COMPLETED else run.analytics_result)
