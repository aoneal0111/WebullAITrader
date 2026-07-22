from app.backtest_run import BacktestRunResult
from app.backtest_report import BacktestReportIdentity,BacktestReportRequest,BacktestReportResult,BacktestReportStatus
from app.backtest_suite.models import *
from app.backtest_suite.validation import validate_dependencies,validate_request
class BacktestSuiteRuntime:
    def __init__(self,run_executor,report_executor):validate_dependencies(run_executor,report_executor);self._runs=run_executor;self._reports=report_executor
    def run(self,request):
        request=validate_request(request,minimal=True)
        if not request.policy.enabled:return self._result(request,BacktestSuiteStatus.DISABLED,(),True,())
        errors=validate_request(request)
        if errors:return self._result(request,BacktestSuiteStatus.REJECTED,(),False,errors)
        if not request.items:return self._result(request,BacktestSuiteStatus.EMPTY,(),True,())
        records=[];stopped=False
        for index,item in enumerate(request.items):
            if stopped:
                records.append(BacktestSuiteItemRecord(index,item.identity,BacktestSuiteItemStatus.SKIPPED,item.run_request,None,None,None,"Skipped because fail-fast policy stopped the suite."));continue
            try:run_result=self._runs.run(item.run_request)
            except Exception as exc:
                records.append(BacktestSuiteItemRecord(index,item.identity,BacktestSuiteItemStatus.RUN_FAILED,item.run_request,None,None,type(exc).__name__,"Backtest run invocation failed."));stopped=request.policy.fail_fast;continue
            if not isinstance(run_result,BacktestRunResult) or run_result.identity.run_id!=item.identity.run_id:
                records.append(BacktestSuiteItemRecord(index,item.identity,BacktestSuiteItemStatus.RUN_REJECTED,item.run_request,run_result if isinstance(run_result,BacktestRunResult) else None,None,"BacktestSuiteResultError","Backtest run result was rejected."));stopped=request.policy.fail_fast;continue
            report_request=BacktestReportRequest(BacktestReportIdentity(item.identity.report_id,item.identity.run_id),run_result,item.report_policy,item.report_requested_at)
            try:report_result=self._reports.create(report_request)
            except Exception as exc:
                records.append(BacktestSuiteItemRecord(index,item.identity,BacktestSuiteItemStatus.REPORT_FAILED,item.run_request,run_result,None,type(exc).__name__,"Backtest report invocation failed."));stopped=request.policy.fail_fast;continue
            if not isinstance(report_result,BacktestReportResult):
                records.append(BacktestSuiteItemRecord(index,item.identity,BacktestSuiteItemStatus.REPORT_FAILED,item.run_request,run_result,None,"BacktestSuiteResultError","Backtest report result was invalid."));stopped=request.policy.fail_fast;continue
            if report_result.status is BacktestReportStatus.COMPLETED:
                status=BacktestSuiteItemStatus.COMPLETED
            elif report_result.status in (BacktestReportStatus.REJECTED,BacktestReportStatus.DISABLED):status=BacktestSuiteItemStatus.REPORT_REJECTED
            else:status=BacktestSuiteItemStatus.REPORT_FAILED
            message=None if status is BacktestSuiteItemStatus.COMPLETED else "Backtest report operation did not complete."
            records.append(BacktestSuiteItemRecord(index,item.identity,status,item.run_request,run_result,report_result,None,message))
            if status is not BacktestSuiteItemStatus.COMPLETED:stopped=request.policy.fail_fast
        return self._result(request,self._status(records),tuple(records),True,())
    @staticmethod
    def _status(records):
        completed=sum(x.status is BacktestSuiteItemStatus.COMPLETED for x in records);skipped=sum(x.status is BacktestSuiteItemStatus.SKIPPED for x in records);failed=len(records)-completed-skipped
        if completed==len(records):return BacktestSuiteStatus.COMPLETED
        if completed:return BacktestSuiteStatus.PARTIALLY_COMPLETED
        return BacktestSuiteStatus.FAILED
    @staticmethod
    def _result(request,status,records,accepted,errors):
        completed=sum(x.status is BacktestSuiteItemStatus.COMPLETED for x in records);skipped=sum(x.status is BacktestSuiteItemStatus.SKIPPED for x in records);failed=len(records)-completed-skipped
        total=0 if status in (BacktestSuiteStatus.DISABLED,BacktestSuiteStatus.REJECTED) else len(request.items)
        summary=BacktestSuiteSummary(total,completed+failed,completed,failed,skipped)
        return BacktestSuiteResult(request.identity,status,request.requested_at,request.completed_at,records,summary,BacktestSuiteCriteriaResult(accepted,tuple(errors)),tuple(errors),None)
