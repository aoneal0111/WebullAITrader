from app.research_study import *
from tests.research_study.helpers import Executor,request,runtime
def test_fail_fast_stops_and_creates_exact_skips():
    req=request(3,fail_fast=True);engine,executor=runtime(Executor(errors={"campaign-1":RuntimeError()}));result=engine.run(req)
    assert len(executor.calls)==2 and tuple(x.status for x in result.campaigns)==(ResearchStudyCampaignStatus.COMPLETED,ResearchStudyCampaignStatus.CAMPAIGN_FAILED,ResearchStudyCampaignStatus.SKIPPED)
    skipped=result.campaigns[2];assert skipped.index==2 and skipped.identity is req.campaigns[2].identity and skipped.campaign_request is req.campaigns[2].campaign_request and skipped.campaign_result is None
    assert skipped.error_type is None and skipped.message=="Skipped because fail-fast policy stopped the research study." and result.status is ResearchStudyStatus.PARTIALLY_COMPLETED
def test_first_failure_is_failed():
    result=runtime(Executor(errors={"campaign-0":RuntimeError()}))[0].run(request(3,fail_fast=True))
    assert result.status is ResearchStudyStatus.FAILED and result.summary==ResearchStudySummary(3,1,0,1,2)
