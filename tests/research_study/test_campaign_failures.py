from app.research_study import *
from tests.research_study.helpers import Executor,request,runtime
def test_continue_after_campaign_exception_is_safe_and_ordered():
    req=request(3);engine,executor=runtime(Executor(errors={"campaign-1":ValueError("secret")}));result=engine.run(req)
    assert len(executor.calls)==3 and tuple(x.status for x in result.campaigns)==(ResearchStudyCampaignStatus.COMPLETED,ResearchStudyCampaignStatus.CAMPAIGN_FAILED,ResearchStudyCampaignStatus.COMPLETED)
    failed=result.campaigns[1];assert failed.identity is req.campaigns[1].identity and failed.campaign_request is req.campaigns[1].campaign_request and failed.campaign_result is None
    assert failed.error_type=="ValueError" and failed.message=="Research campaign invocation failed." and "secret" not in str(result.to_dict())
def test_invalid_campaign_results_are_failed():
    result=runtime(Executor(callback=lambda ignored:object()))[0].run(request(2))
    assert result.status is ResearchStudyStatus.FAILED and all(x.error_type=="InvalidResearchCampaignResult" and x.message=="Research campaign returned an invalid result." for x in result.campaigns)
