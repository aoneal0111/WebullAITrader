from app.backtest_report.exceptions import BacktestReportSerializationError
from app.backtest_report.models import *
def _s(v,t):
    if not isinstance(v,t):raise BacktestReportSerializationError(f"value must be {t.__name__}")
    return v.to_dict()
serialize_identity=lambda v:_s(v,BacktestReportIdentity)
serialize_policy=lambda v:_s(v,BacktestReportPolicy)
serialize_request=lambda v:_s(v,BacktestReportRequest)
serialize_overview=lambda v:_s(v,BacktestReportOverview)
serialize_stage=lambda v:_s(v,BacktestReportStageSummary)
serialize_activity=lambda v:_s(v,BacktestReportActivitySummary)
serialize_performance=lambda v:_s(v,BacktestReportPerformanceSummary)
serialize_issues=lambda v:_s(v,BacktestReportIssueSummary)
serialize_report=lambda v:_s(v,BacktestReport)
serialize_result=lambda v:_s(v,BacktestReportResult)
