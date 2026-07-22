from app.backtest_report import *
from tests.backtest_report.helpers import request,source
def test_completed_report_preserves_exact_source_and_analytics():
    run=source(2);result=BacktestReportRuntime().create(request(run));report=result.report
    assert result.status is BacktestReportStatus.COMPLETED and report.status is BacktestReportStatus.COMPLETED
    assert report.source_run_result is run and report.performance.analytics_result is run.analytics_result
    assert report.overview.run_requested_at is run.requested_at and report.overview.run_completed_at is run.completed_at
def test_activity_uses_public_counts():
    run=source(2);activity=BacktestReportRuntime().create(request(run)).report.activity
    assert activity.replay_event_count==run.replay_result.progress.total_events==2
    assert activity.projected_cycle_count==len(run.projection_result.cycles)==2
    assert activity.journal_attempt_count==run.journal_batch_result.progress.total_count==2 and activity.journal_success_count==2
def test_stage_order_and_policy_inclusion():
    run=source(1);report=BacktestReportRuntime().create(request(run)).report
    assert tuple(x.stage for x in report.stages)==tuple(x.stage for x in run.stage_results)
    policy=BacktestReportPolicy(include_stage_history=False,include_warnings=False,include_errors=False)
    excluded=BacktestReportRuntime().create(request(run,policy)).report
    assert excluded.stages==() and excluded.issues.warnings==() and excluded.issues.errors==()
def test_repeated_calls_deterministic_and_stateless():
    req=request();runtime=BacktestReportRuntime();a=runtime.create(req);b=runtime.create(req);assert a==b and a.to_dict()==b.to_dict()
