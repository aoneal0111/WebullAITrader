from dataclasses import FrozenInstanceError,replace
from datetime import datetime,timedelta
import pytest
from app.research_study import *
from tests.research_campaign.helpers import runtime as campaign_runtime
from tests.research_study.helpers import Executor,request,runtime
def test_models_policy_and_immutability():
    assert len(ResearchStudyStatus)==6 and len(ResearchStudyCampaignStatus)==3
    assert ResearchStudyPolicy()==ResearchStudyPolicy.from_dict(ResearchStudyPolicy().to_dict())
    req=request(1);result=runtime()[0].run(req)
    for model,field,value in ((req.identity,"study_id","x"),(req.campaigns[0].identity,"campaign_entry_id","x"),(req,"campaigns",()),(result.campaigns[0],"index",2),(result.summary,"total_campaigns",2),(result,"status",ResearchStudyStatus.FAILED)):
        with pytest.raises(FrozenInstanceError):setattr(model,field,value)
    assert isinstance(req.campaigns,tuple) and isinstance(result.campaigns,tuple)
def test_serialization_is_stable_and_nested():
    req=request(2);result=runtime()[0].run(req);data=serialize_result(result)
    assert data==serialize_result(result)==result.to_dict() and serialize_request(req)==req.to_dict()
    assert data["status"]=="COMPLETED" and data["requested_at"]==req.requested_at.isoformat()
    assert data["campaigns"][0]["campaign_request"]==req.campaigns[0].campaign_request.to_dict()
    assert data["campaigns"][0]["campaign_result"]==result.campaigns[0].campaign_result.to_dict()
def test_invalid_models_returns_and_serializers():
    with pytest.raises(ResearchStudyValidationError):ResearchStudyIdentity("")
    with pytest.raises(ResearchStudyValidationError):ResearchStudyPolicy(enabled=1)
    with pytest.raises(ResearchStudyValidationError):ResearchStudySummary(1,1,1,1,0)
    with pytest.raises(ResearchStudyValidationError):ResearchStudySummary(-1,0,0,0,0)
    req=request(1)
    with pytest.raises(ResearchStudyValidationError):replace(req,requested_at=datetime.now(),completed_at=datetime.now())
    with pytest.raises(ResearchStudyValidationError):replace(req,completed_at=req.requested_at-timedelta(seconds=1))
    with pytest.raises(ResearchStudyValidationError):replace(req,campaigns=[])
    with pytest.raises(ResearchStudyValidationError):replace(req.campaigns[0],campaign_request=object())
    with pytest.raises(ResearchStudyValidationError):ResearchStudyCampaignRecord(-1,req.campaigns[0].identity,ResearchStudyCampaignStatus.COMPLETED,req.campaigns[0].campaign_request,None)
    valid=runtime()[0].run(req)
    with pytest.raises(ResearchStudyValidationError):replace(valid.campaigns[0],campaign_result=object())
    with pytest.raises(ResearchStudyValidationError):replace(valid.campaigns[0],index=True)
    with pytest.raises(ResearchStudyValidationError):replace(valid,campaigns=(replace(valid.campaigns[0],index=1),))
    serializers=(serialize_policy,serialize_identity,serialize_campaign_identity,serialize_campaign_request,serialize_request,serialize_criteria,serialize_campaign_record,serialize_summary,serialize_result)
    for serializer in serializers:
        with pytest.raises(ResearchStudySerializationError):serializer({})
    failed=runtime(Executor(callback=lambda ignored:object()))[0].run(req);assert serialize_result(failed)["campaigns"][0]["campaign_result"] is None
def test_public_exports():
    import app.research_study as package
    expected=("ResearchStudyRuntime","ResearchCampaignExecutor","ResearchStudyStatus","ResearchStudyCampaignStatus","ResearchStudyPolicy","ResearchStudyIdentity","ResearchStudyCampaignIdentity","ResearchStudyCampaignRequest","ResearchStudyRequest","ResearchStudyCriteriaResult","ResearchStudyCampaignRecord","ResearchStudySummary","ResearchStudyResult","serialize_request","serialize_result","validate_request")
    assert all(name in package.__all__ and hasattr(package,name) for name in expected)
    class CompatibleExecutor:
        def run(self,child):return campaign_runtime()[0].run(child)
    assert isinstance(ResearchStudyRuntime(CompatibleExecutor()),ResearchStudyRuntime)
