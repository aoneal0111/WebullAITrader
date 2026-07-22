from dataclasses import FrozenInstanceError,replace
import pytest
from app.research_campaign import *
from tests.experiment.helpers import runtime as experiment_runtime
from tests.research_campaign.helpers import Executor,request,runtime
def test_enums_defaults_and_immutable_models():
    assert len(ResearchCampaignStatus)==6 and len(ResearchCampaignExperimentStatus)==3
    assert ResearchCampaignPolicy()==ResearchCampaignPolicy.from_dict(ResearchCampaignPolicy().to_dict())
    req=request(1);result=runtime()[0].run(req)
    for model,field,value in ((req.identity,"campaign_id","changed"),(req.experiments[0].identity,"experiment_entry_id","changed"),(req,"experiments",()),(result.experiments[0],"index",3),(result.summary,"total_experiments",3),(result,"status",ResearchCampaignStatus.FAILED)):
        with pytest.raises(FrozenInstanceError):setattr(model,field,value)
    assert isinstance(req.experiments,tuple) and isinstance(result.experiments,tuple) and isinstance(result.errors,tuple)
def test_serialization_is_stable_nested_and_public():
    req=request(2);result=runtime()[0].run(req);serialized=serialize_result(result)
    assert serialize_request(req)==req.to_dict() and serialized==serialize_result(result)==result.to_dict()
    assert serialized["status"]=="COMPLETED" and serialized["requested_at"]==req.requested_at.isoformat()
    assert [x["identity"]["experiment_entry_id"] for x in serialized["experiments"]]==[x.identity.experiment_entry_id for x in req.experiments]
    assert serialized["experiments"][0]["experiment_request"]==req.experiments[0].experiment_request.to_dict()
    assert serialized["experiments"][0]["experiment_result"]==result.experiments[0].experiment_result.to_dict()
def test_none_result_and_safe_failure_serialization():
    result=runtime(Executor(callback=lambda ignored:object()))[0].run(request(1));record=serialize_result(result)["experiments"][0]
    assert record["experiment_result"] is None and record["error_type"]=="InvalidExperimentResult"
def test_invalid_models_and_serializer():
    with pytest.raises(ResearchCampaignValidationError):ResearchCampaignIdentity("")
    with pytest.raises(ResearchCampaignValidationError):ResearchCampaignPolicy(enabled=1)
    with pytest.raises(ResearchCampaignValidationError):ResearchCampaignSummary(1,1,1,1,0)
    req=request(1)
    with pytest.raises(ResearchCampaignValidationError):replace(req,experiments=[])
    with pytest.raises(ResearchCampaignValidationError):replace(req.experiments[0],experiment_request=object())
    with pytest.raises(ResearchCampaignValidationError):ResearchCampaignExperimentRecord(-1,req.experiments[0].identity,ResearchCampaignExperimentStatus.COMPLETED,req.experiments[0].experiment_request,None)
    result=runtime()[0].run(req)
    with pytest.raises(ResearchCampaignValidationError):replace(result.experiments[0],experiment_result=object())
    with pytest.raises(ResearchCampaignSerializationError):serialize_result({})
def test_public_exports_and_protocol_compatible_executor():
    import app.research_campaign as package
    expected=("ResearchCampaignError","ResearchCampaignValidationError","ResearchCampaignDependencyError","ExperimentExecutor","ResearchCampaignStatus","ResearchCampaignExperimentStatus","ResearchCampaignPolicy","ResearchCampaignIdentity","ResearchCampaignExperimentIdentity","ResearchCampaignExperimentRequest","ResearchCampaignRequest","ResearchCampaignCriteriaResult","ResearchCampaignExperimentRecord","ResearchCampaignSummary","ResearchCampaignResult","ResearchCampaignRuntime","serialize_request","serialize_result","validate_request")
    assert all(name in package.__all__ and hasattr(package,name) for name in expected)
    class Compatible:
        def run(self,child):return experiment_runtime()[0].run(child)
    assert isinstance(ResearchCampaignRuntime(Compatible()),ResearchCampaignRuntime)
