from app.research_study import ResearchStudyCampaignStatus,ResearchStudyStatus
from tests.research_study.helpers import Executor,request,runtime
def test_runtime_has_no_cross_call_state_or_cache():
    executor=Executor(errors={"campaign-0":RuntimeError()});engine,_=runtime(executor);failed=engine.run(request(2,fail_fast=True));executor.errors={};success=engine.run(request(2,fail_fast=True));again=engine.run(request(2,fail_fast=True))
    assert tuple(x.status for x in failed.campaigns)==(ResearchStudyCampaignStatus.CAMPAIGN_FAILED,ResearchStudyCampaignStatus.SKIPPED)
    assert success.status is ResearchStudyStatus.COMPLETED and success==again and success is not again
    assert failed.campaigns is not success.campaigns and success.campaigns is not again.campaigns and success.summary is not again.summary
