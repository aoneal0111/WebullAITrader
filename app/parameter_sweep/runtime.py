from app.backtest_suite import BacktestSuiteResult
from app.parameter_sweep.models import *
from app.parameter_sweep.validation import validate_dependencies,validate_request
class ParameterSweepRuntime:
    def __init__(self,suite_executor):validate_dependencies(suite_executor);self._executor=suite_executor
    def run(self,request):
        request=validate_request(request,minimal=True)
        if not request.policy.enabled:return self._result(request,ParameterSweepStatus.DISABLED,(),True,())
        errors=validate_request(request)
        if errors:return self._result(request,ParameterSweepStatus.REJECTED,(),False,errors)
        if not request.cases:return self._result(request,ParameterSweepStatus.EMPTY,(),True,())
        records=[];stopped=False
        for index,case in enumerate(request.cases):
            if stopped:
                records.append(ParameterSweepCaseRecord(index,case.identity,ParameterSweepCaseStatus.SKIPPED,case.suite_request,None,None,"Skipped because fail-fast policy stopped the sweep."));continue
            try:result=self._executor.run(case.suite_request)
            except Exception as exc:
                records.append(ParameterSweepCaseRecord(index,case.identity,ParameterSweepCaseStatus.SUITE_FAILED,case.suite_request,None,type(exc).__name__,"Backtest suite invocation failed."));stopped=request.policy.fail_fast;continue
            if not isinstance(result,BacktestSuiteResult):
                records.append(ParameterSweepCaseRecord(index,case.identity,ParameterSweepCaseStatus.SUITE_FAILED,case.suite_request,None,"InvalidBacktestSuiteResult","Backtest suite returned an invalid result."));stopped=request.policy.fail_fast;continue
            records.append(ParameterSweepCaseRecord(index,case.identity,ParameterSweepCaseStatus.COMPLETED,case.suite_request,result,None,None))
        return self._result(request,self._status(records),tuple(records),True,())
    @staticmethod
    def _status(records):
        completed=sum(x.status is ParameterSweepCaseStatus.COMPLETED for x in records)
        if completed==len(records):return ParameterSweepStatus.COMPLETED
        if completed:return ParameterSweepStatus.PARTIALLY_COMPLETED
        return ParameterSweepStatus.FAILED
    @staticmethod
    def _result(request,status,records,accepted,errors):
        completed=sum(x.status is ParameterSweepCaseStatus.COMPLETED for x in records);skipped=sum(x.status is ParameterSweepCaseStatus.SKIPPED for x in records);failed=len(records)-completed-skipped
        total=0 if status in (ParameterSweepStatus.DISABLED,ParameterSweepStatus.REJECTED) else len(request.cases)
        summary=ParameterSweepSummary(total,completed+failed,completed,failed,skipped)
        return ParameterSweepResult(request.identity,status,request.requested_at,request.completed_at,records,summary,ParameterSweepCriteriaResult(accepted,tuple(errors)),tuple(errors),None)
