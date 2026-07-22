from app.research_campaign import ResearchCampaignExperimentStatus,ResearchCampaignStatus
from tests.research_campaign.helpers import Executor,request,runtime
def test_same_runtime_has_no_cross_call_state():
    executor=Executor(errors={"experiment-0":RuntimeError()});engine,_=runtime(executor)
    failed=engine.run(request(2,fail_fast=True));executor.errors={};completed=engine.run(request(2,fail_fast=True));empty=engine.run(request(0))
    assert tuple(x.status for x in failed.experiments)==(ResearchCampaignExperimentStatus.EXPERIMENT_FAILED,ResearchCampaignExperimentStatus.SKIPPED)
    assert completed.status is ResearchCampaignStatus.COMPLETED and empty.status is ResearchCampaignStatus.EMPTY
    assert failed.experiments is not completed.experiments and failed.summary is not completed.summary and empty.experiments==()
def test_equivalent_executions_on_same_runtime_are_equal_without_cached_results():
    engine,_=runtime();req=request(2);first=engine.run(req);second=engine.run(req)
    assert first==second and first is not second and first.experiments is not second.experiments and first.summary is not second.summary
