from app.backtest_suite.exceptions import BacktestSuiteDependencyError,BacktestSuiteValidationError
from app.backtest_suite.models import BacktestSuiteRequest
def validate_dependencies(run_executor,report_executor):
    if run_executor is None or not callable(getattr(run_executor,"run",None)) or report_executor is None or not callable(getattr(report_executor,"create",None)):raise BacktestSuiteDependencyError("run and report executors are required")
def validate_request(request,minimal=False):
    if not isinstance(request,BacktestSuiteRequest):raise BacktestSuiteValidationError("request must be BacktestSuiteRequest")
    if minimal:return request
    errors=[];seen_items=set();seen_runs=set();seen_reports=set()
    for item in request.items:
        if item.identity.run_id!=item.run_request.identity.run_id:errors.append(f"run identity mismatch at item {item.identity.item_id}")
        for value,seen,label in ((item.identity.item_id,seen_items,"item ID"),(item.identity.run_id,seen_runs,"run ID"),(item.identity.report_id,seen_reports,"report ID")):
            if value in seen:errors.append(f"duplicate {label} at item {item.identity.item_id}")
            seen.add(value)
    return tuple(errors)
