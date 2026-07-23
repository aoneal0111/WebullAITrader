from app.research_campaign import ResearchCampaignResult
from app.research_study.models import *
from app.research_study.validation import validate_dependencies,validate_request
class ResearchStudyRuntime:
    def __init__(self,campaign_executor):validate_dependencies(campaign_executor);self._executor=campaign_executor
    def run(self,request):
        request=validate_request(request,minimal=True)
        if not request.policy.enabled:return self._result(request,ResearchStudyStatus.DISABLED,(),True,())
        errors=validate_request(request)
        if errors:return self._result(request,ResearchStudyStatus.REJECTED,(),False,errors)
        if not request.campaigns:return self._result(request,ResearchStudyStatus.EMPTY,(),True,())
        records=[];stopped=False
        for index,campaign in enumerate(request.campaigns):
            if stopped:
                records.append(ResearchStudyCampaignRecord(index,campaign.identity,ResearchStudyCampaignStatus.SKIPPED,campaign.campaign_request,None,None,"Skipped because fail-fast policy stopped the research study."));continue
            try:result=self._executor.run(campaign.campaign_request)
            except Exception as exc:
                records.append(ResearchStudyCampaignRecord(index,campaign.identity,ResearchStudyCampaignStatus.CAMPAIGN_FAILED,campaign.campaign_request,None,type(exc).__name__,"Research campaign invocation failed."));stopped=request.policy.fail_fast;continue
            if not isinstance(result,ResearchCampaignResult):
                records.append(ResearchStudyCampaignRecord(index,campaign.identity,ResearchStudyCampaignStatus.CAMPAIGN_FAILED,campaign.campaign_request,None,"InvalidResearchCampaignResult","Research campaign returned an invalid result."));stopped=request.policy.fail_fast;continue
            records.append(ResearchStudyCampaignRecord(index,campaign.identity,ResearchStudyCampaignStatus.COMPLETED,campaign.campaign_request,result,None,None))
        return self._result(request,self._status(records),tuple(records),True,())
    @staticmethod
    def _status(records):
        completed=sum(x.status is ResearchStudyCampaignStatus.COMPLETED for x in records)
        if completed==len(records):return ResearchStudyStatus.COMPLETED
        if completed:return ResearchStudyStatus.PARTIALLY_COMPLETED
        return ResearchStudyStatus.FAILED
    @staticmethod
    def _result(request,status,records,accepted,errors):
        completed=sum(x.status is ResearchStudyCampaignStatus.COMPLETED for x in records);skipped=sum(x.status is ResearchStudyCampaignStatus.SKIPPED for x in records);failed=len(records)-completed-skipped
        total=0 if status in (ResearchStudyStatus.DISABLED,ResearchStudyStatus.REJECTED) else len(request.campaigns)
        summary=ResearchStudySummary(total,completed+failed,completed,failed,skipped)
        return ResearchStudyResult(request.identity,status,request.requested_at,request.completed_at,records,summary,ResearchStudyCriteriaResult(accepted,tuple(errors)),tuple(errors),None)
