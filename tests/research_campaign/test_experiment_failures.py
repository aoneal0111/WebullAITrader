from app.research_campaign import *
from tests.research_campaign.helpers import Executor,request,runtime
def test_continue_after_exception_with_safe_normalization():
    req=request(3);engine,executor=runtime(Executor(errors={"experiment-1":ValueError("secret path and token")}));result=engine.run(req)
    assert len(executor.calls)==3
    assert tuple(x.status for x in result.experiments)==(ResearchCampaignExperimentStatus.COMPLETED,ResearchCampaignExperimentStatus.EXPERIMENT_FAILED,ResearchCampaignExperimentStatus.COMPLETED)
    assert result.status is ResearchCampaignStatus.PARTIALLY_COMPLETED and result.experiments[1].error_type=="ValueError"
    assert result.experiments[1].message=="Experiment invocation failed." and "secret" not in str(result.to_dict())
    assert result.experiments[1].identity is req.experiments[1].identity and result.experiments[1].experiment_request is req.experiments[1].experiment_request
    assert result.experiments[1].experiment_result is None and executor.calls.count(req.experiments[1].experiment_request)==1
def test_invalid_returns_are_deterministic_failures():
    result=runtime(Executor(callback=lambda ignored:object()))[0].run(request(2))
    assert result.status is ResearchCampaignStatus.FAILED
    assert all(x.experiment_result is None and x.error_type=="InvalidExperimentResult" and x.message=="Experiment returned an invalid result." for x in result.experiments)
