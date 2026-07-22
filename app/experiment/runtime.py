from app.parameter_sweep import ParameterSweepResult
from app.experiment.models import *
from app.experiment.validation import validate_dependencies,validate_request
class ExperimentRuntime:
    def __init__(self,sweep_executor):validate_dependencies(sweep_executor);self._executor=sweep_executor
    def run(self,request):
        request=validate_request(request,minimal=True)
        if not request.policy.enabled:return self._result(request,ExperimentStatus.DISABLED,(),True,())
        errors=validate_request(request)
        if errors:return self._result(request,ExperimentStatus.REJECTED,(),False,errors)
        if not request.sweeps:return self._result(request,ExperimentStatus.EMPTY,(),True,())
        records=[];stopped=False
        for index,sweep in enumerate(request.sweeps):
            if stopped:
                records.append(ExperimentSweepRecord(index,sweep.identity,ExperimentSweepStatus.SKIPPED,sweep.parameter_sweep_request,None,None,"Skipped because fail-fast policy stopped the experiment."));continue
            try:result=self._executor.run(sweep.parameter_sweep_request)
            except Exception as exc:
                records.append(ExperimentSweepRecord(index,sweep.identity,ExperimentSweepStatus.SWEEP_FAILED,sweep.parameter_sweep_request,None,type(exc).__name__,"Parameter sweep invocation failed."));stopped=request.policy.fail_fast;continue
            if not isinstance(result,ParameterSweepResult):
                records.append(ExperimentSweepRecord(index,sweep.identity,ExperimentSweepStatus.SWEEP_FAILED,sweep.parameter_sweep_request,None,"InvalidParameterSweepResult","Parameter sweep returned an invalid result."));stopped=request.policy.fail_fast;continue
            records.append(ExperimentSweepRecord(index,sweep.identity,ExperimentSweepStatus.COMPLETED,sweep.parameter_sweep_request,result,None,None))
        return self._result(request,self._status(records),tuple(records),True,())
    @staticmethod
    def _status(records):
        completed=sum(x.status is ExperimentSweepStatus.COMPLETED for x in records)
        if completed==len(records):return ExperimentStatus.COMPLETED
        if completed:return ExperimentStatus.PARTIALLY_COMPLETED
        return ExperimentStatus.FAILED
    @staticmethod
    def _result(request,status,records,accepted,errors):
        completed=sum(x.status is ExperimentSweepStatus.COMPLETED for x in records);skipped=sum(x.status is ExperimentSweepStatus.SKIPPED for x in records);failed=len(records)-completed-skipped
        total=0 if status in (ExperimentStatus.DISABLED,ExperimentStatus.REJECTED) else len(request.sweeps)
        summary=ExperimentSummary(total,completed+failed,completed,failed,skipped)
        return ExperimentResult(request.identity,status,request.requested_at,request.completed_at,records,summary,ExperimentCriteriaResult(accepted,tuple(errors)),tuple(errors),None)
