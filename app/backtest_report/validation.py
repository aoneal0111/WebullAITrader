from app.backtest_run import BacktestRunStatus
from app.backtest_report.exceptions import BacktestReportValidationError
from app.backtest_report.models import BacktestReportRequest
def validate_request(request,minimal=False):
    if not isinstance(request,BacktestReportRequest):raise BacktestReportValidationError("request must be BacktestReportRequest")
    if minimal:return request
    run=request.run_result
    if request.identity.run_id!=run.identity.run_id:raise BacktestReportValidationError("run identity mismatch")
    if run.status is BacktestRunStatus.COMPLETED and (run.replay_result is None or run.projection_result is None or run.journal_batch_result is None or run.analytics_result is None):raise BacktestReportValidationError("completed run is missing required results")
    return request
