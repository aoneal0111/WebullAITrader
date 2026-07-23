from dataclasses import replace
from app.experiment import ExperimentStatus
from app.research_campaign import *
from tests.experiment.helpers import runtime as experiment_runtime
from tests.research_campaign.helpers import Executor,request,runtime
def test_exact_order_request_and_result_continuity():
    req=request(3);known=tuple(experiment_runtime()[0].run(x.experiment_request) for x in req.experiments);by_id={x.identity.experiment_id:x for x in known}
    engine,executor=runtime(Executor(callback=lambda child:by_id[child.identity.experiment_id]));result=engine.run(req)
    assert executor.calls==[x.experiment_request for x in req.experiments]
    assert all(executor.calls[i] is req.experiments[i].experiment_request for i in range(3))
    assert all(result.experiments[i].experiment_result is known[i] for i in range(3))
    assert all(result.experiments[i].identity is req.experiments[i].identity for i in range(3))
    assert result.status is ResearchCampaignStatus.COMPLETED and result.summary==ResearchCampaignSummary(3,3,3,0,0)
    assert result.identity is req.identity and result.requested_at is req.requested_at and result.completed_at is req.completed_at
def test_every_valid_child_status_is_completed_orchestration():
    for status in ExperimentStatus:
        executor=Executor(callback=lambda req,status=status:replace(experiment_runtime()[0].run(req),status=status))
        result=runtime(executor)[0].run(request(1,fail_fast=True))
        assert result.experiments[0].status is ResearchCampaignExperimentStatus.COMPLETED and result.status is ResearchCampaignStatus.COMPLETED
def test_valid_failed_child_status_does_not_trigger_fail_fast():
    calls=[]
    def return_failed(req):
        calls.append(req)
        return replace(experiment_runtime()[0].run(req),status=ExperimentStatus.FAILED)
    req=request(3,fail_fast=True);result=runtime(Executor(callback=return_failed))[0].run(req)
    assert calls==[x.experiment_request for x in req.experiments]
    assert all(x.status is ResearchCampaignExperimentStatus.COMPLETED for x in result.experiments)
    assert result.status is ResearchCampaignStatus.COMPLETED
def test_repeated_equal_inputs_are_deterministic():
    req=request(2);a=runtime()[0].run(req);b=runtime()[0].run(req)
    assert a==b and serialize_result(a)==serialize_result(b)
