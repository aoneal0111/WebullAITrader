from dataclasses import replace
from app.research_campaign import ResearchCampaignStatus
from app.research_study import *
from tests.research_campaign.helpers import runtime as campaign_runtime
from tests.research_study.helpers import Executor,request,runtime
def test_order_and_exact_object_continuity():
    req=request(3);known=tuple(campaign_runtime()[0].run(x.campaign_request) for x in req.campaigns);by_id={x.identity.campaign_id:x for x in known}
    engine,executor=runtime(Executor(callback=lambda child:by_id[child.identity.campaign_id]));result=engine.run(req)
    assert all(executor.calls[i] is req.campaigns[i].campaign_request for i in range(3))
    assert all(result.campaigns[i].campaign_result is known[i] and result.campaigns[i].identity is req.campaigns[i].identity for i in range(3))
    assert result.status is ResearchStudyStatus.COMPLETED and result.summary==ResearchStudySummary(3,3,3,0,0)
    assert result.identity is req.identity and result.requested_at is req.requested_at and result.completed_at is req.completed_at
def test_all_valid_campaign_statuses_complete_orchestration_and_never_fail_fast():
    for status in ResearchCampaignStatus:
        calls=[]
        def callback(req,status=status):calls.append(req);return replace(campaign_runtime()[0].run(req),status=status)
        req=request(2,fail_fast=True);result=runtime(Executor(callback=callback))[0].run(req)
        assert len(calls)==2 and all(x.status is ResearchStudyCampaignStatus.COMPLETED for x in result.campaigns)
def test_repeatability():
    req=request(2);a=runtime()[0].run(req);b=runtime()[0].run(req)
    assert a==b and a is not b and a.campaigns is not b.campaigns and a.summary is not b.summary
    assert serialize_result(a)==serialize_result(b)
