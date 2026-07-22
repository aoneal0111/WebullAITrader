from app.backtest_run import BacktestRunStatus
from app.backtest_report.models import *
from app.backtest_report.validation import validate_request
class BacktestReportRuntime:
    def create(self,request):
        request=validate_request(request,minimal=True)
        if not request.policy.enabled:return BacktestReportResult(request.identity,BacktestReportStatus.DISABLED,None,request.requested_at,(BacktestReportCriteriaResult("policy_enabled",False,("reporting disabled",)),))
        try:validate_request(request)
        except Exception:return BacktestReportResult(request.identity,BacktestReportStatus.REJECTED,None,request.requested_at,(BacktestReportCriteriaResult("request_valid",False,("report request rejected",)),),("Backtest report validation rejected.",),None)
        run=request.run_result;status=self._status(run.status)
        overview=BacktestReportOverview(request.identity.report_id,request.identity.run_id,run.identity.source_id,run.status,status,run.stopped_at,run.requested_at,run.completed_at,request.requested_at)
        replay_count=run.replay_result.progress.total_events if run.replay_result else None
        projection_count=len(run.projection_result.cycles) if run.projection_result else None
        progress=run.journal_batch_result.progress if run.journal_batch_result else None
        activity=BacktestReportActivitySummary(replay_count,projection_count,progress.total_count if progress else None,progress.completed_count if progress else None,progress.failed_count if progress else None)
        performance=BacktestReportPerformanceSummary(run.analytics_result)
        stages=tuple(BacktestReportStageSummary(x.stage,x.status,x.message,x.error_type) for x in run.stage_results) if request.policy.include_stage_history else ()
        issues=BacktestReportIssueSummary(run.warnings if request.policy.include_warnings else (),run.errors if request.policy.include_errors else (),run.error_type if request.policy.include_errors else None)
        report=BacktestReport(request.identity,status,overview,activity,performance,stages,issues,run)
        return BacktestReportResult(request.identity,BacktestReportStatus.COMPLETED,report,request.requested_at,(BacktestReportCriteriaResult("request_valid",True,()),BacktestReportCriteriaResult("report_constructed",True,())))
    @staticmethod
    def _status(status):return {BacktestRunStatus.COMPLETED:BacktestReportStatus.COMPLETED,BacktestRunStatus.PARTIALLY_COMPLETED:BacktestReportStatus.PARTIAL,BacktestRunStatus.EMPTY:BacktestReportStatus.EMPTY,BacktestRunStatus.DISABLED:BacktestReportStatus.DISABLED,BacktestRunStatus.REJECTED:BacktestReportStatus.REJECTED,BacktestRunStatus.FAILED:BacktestReportStatus.FAILED}[status]
