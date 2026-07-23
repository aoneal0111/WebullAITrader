from app.research_campaign import *
from tests.research_campaign.helpers import Executor,request,runtime
def test_fail_fast_stops_calls_and_emits_ordered_skips():
    req=request(3,fail_fast=True);engine,executor=runtime(Executor(errors={"experiment-1":RuntimeError()}));result=engine.run(req)
    assert len(executor.calls)==2
    assert tuple(x.status for x in result.experiments)==(ResearchCampaignExperimentStatus.COMPLETED,ResearchCampaignExperimentStatus.EXPERIMENT_FAILED,ResearchCampaignExperimentStatus.SKIPPED)
    assert result.experiments[2].experiment_request is req.experiments[2].experiment_request and result.experiments[2].experiment_result is None
    assert result.experiments[2].identity is req.experiments[2].identity and result.experiments[2].index==2 and result.experiments[2].error_type is None
    assert result.experiments[2].message=="Skipped because fail-fast policy stopped the research campaign."
    assert result.status is ResearchCampaignStatus.PARTIALLY_COMPLETED
def test_first_failure_means_failed_and_remaining_skipped():
    result=runtime(Executor(errors={"experiment-0":RuntimeError()}))[0].run(request(3,fail_fast=True))
    assert result.status is ResearchCampaignStatus.FAILED and result.summary==ResearchCampaignSummary(3,1,0,1,2)
