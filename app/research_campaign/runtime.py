from app.experiment import ExperimentResult
from app.research_campaign.models import *
from app.research_campaign.validation import validate_dependencies,validate_request
class ResearchCampaignRuntime:
    def __init__(self,experiment_executor):validate_dependencies(experiment_executor);self._executor=experiment_executor
    def run(self,request):
        request=validate_request(request,minimal=True)
        if not request.policy.enabled:return self._result(request,ResearchCampaignStatus.DISABLED,(),True,())
        errors=validate_request(request)
        if errors:return self._result(request,ResearchCampaignStatus.REJECTED,(),False,errors)
        if not request.experiments:return self._result(request,ResearchCampaignStatus.EMPTY,(),True,())
        records=[];stopped=False
        for index,experiment in enumerate(request.experiments):
            if stopped:
                records.append(ResearchCampaignExperimentRecord(index,experiment.identity,ResearchCampaignExperimentStatus.SKIPPED,experiment.experiment_request,None,None,"Skipped because fail-fast policy stopped the research campaign."));continue
            try:result=self._executor.run(experiment.experiment_request)
            except Exception as exc:
                records.append(ResearchCampaignExperimentRecord(index,experiment.identity,ResearchCampaignExperimentStatus.EXPERIMENT_FAILED,experiment.experiment_request,None,type(exc).__name__,"Experiment invocation failed."));stopped=request.policy.fail_fast;continue
            if not isinstance(result,ExperimentResult):
                records.append(ResearchCampaignExperimentRecord(index,experiment.identity,ResearchCampaignExperimentStatus.EXPERIMENT_FAILED,experiment.experiment_request,None,"InvalidExperimentResult","Experiment returned an invalid result."));stopped=request.policy.fail_fast;continue
            records.append(ResearchCampaignExperimentRecord(index,experiment.identity,ResearchCampaignExperimentStatus.COMPLETED,experiment.experiment_request,result,None,None))
        return self._result(request,self._status(records),tuple(records),True,())
    @staticmethod
    def _status(records):
        completed=sum(x.status is ResearchCampaignExperimentStatus.COMPLETED for x in records)
        if completed==len(records):return ResearchCampaignStatus.COMPLETED
        if completed:return ResearchCampaignStatus.PARTIALLY_COMPLETED
        return ResearchCampaignStatus.FAILED
    @staticmethod
    def _result(request,status,records,accepted,errors):
        completed=sum(x.status is ResearchCampaignExperimentStatus.COMPLETED for x in records);skipped=sum(x.status is ResearchCampaignExperimentStatus.SKIPPED for x in records);failed=len(records)-completed-skipped
        total=0 if status in (ResearchCampaignStatus.DISABLED,ResearchCampaignStatus.REJECTED) else len(request.experiments)
        summary=ResearchCampaignSummary(total,completed+failed,completed,failed,skipped)
        return ResearchCampaignResult(request.identity,status,request.requested_at,request.completed_at,records,summary,ResearchCampaignCriteriaResult(accepted,tuple(errors)),tuple(errors),None)
