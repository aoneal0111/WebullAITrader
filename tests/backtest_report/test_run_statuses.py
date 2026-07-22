import pytest
from app.backtest_report import *
from app.backtest_run import BacktestRunStatus
from tests.backtest_report.helpers import request,with_status
@pytest.mark.parametrize("source_status,report_status",[(BacktestRunStatus.COMPLETED,BacktestReportStatus.COMPLETED),(BacktestRunStatus.PARTIALLY_COMPLETED,BacktestReportStatus.PARTIAL),(BacktestRunStatus.EMPTY,BacktestReportStatus.EMPTY),(BacktestRunStatus.DISABLED,BacktestReportStatus.DISABLED),(BacktestRunStatus.REJECTED,BacktestReportStatus.REJECTED),(BacktestRunStatus.FAILED,BacktestReportStatus.FAILED)])
def test_every_source_status_produces_valid_report(source_status,report_status):
    run=with_status(source_status);result=BacktestReportRuntime().create(request(run))
    assert result.status is BacktestReportStatus.COMPLETED and result.report.status is report_status and result.report.source_run_result is run
def test_unreached_stages_are_none_not_zero():
    run=with_status(BacktestRunStatus.FAILED);run=__import__('dataclasses').replace(run,replay_result=None,projection_result=None,journal_batch_result=None,analytics_result=None)
    activity=BacktestReportRuntime().create(request(run)).report.activity
    assert activity.replay_event_count is None and activity.projected_cycle_count is None and activity.journal_attempt_count is None
