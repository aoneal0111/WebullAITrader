from dataclasses import FrozenInstanceError,replace
from datetime import datetime,timedelta
import pytest
from app.research_program import *
from tests.research_program.helpers import Executor,request,runtime
def test_models_policy_and_serialization():
    assert len(ResearchProgramStatus)==6 and len(ResearchProgramStudyStatus)==3
    assert ResearchProgramPolicy()==ResearchProgramPolicy.from_dict(ResearchProgramPolicy().to_dict())
    req=request(2);result=runtime()[0].run(req);data=serialize_result(result)
    assert data==serialize_result(result)==result.to_dict() and serialize_request(req)==req.to_dict()
    assert data["status"]=="COMPLETED" and data["requested_at"]==req.requested_at.isoformat()
    assert data["studies"][0]["study_request"]==req.studies[0].study_request.to_dict() and data["studies"][0]["study_result"]==result.studies[0].study_result.to_dict()
    assert req.identity.program_id=="program-1" and req.studies[0].study_request is req.studies[0].study_request
    for model,field,value in ((req.identity,"program_id","x"),(req.studies[0].identity,"study_entry_id","x"),(req,"studies",()),(result.studies[0],"index",2),(result.summary,"total_studies",2),(result,"status",ResearchProgramStatus.FAILED)):
        with pytest.raises(FrozenInstanceError):setattr(model,field,value)
def test_invalid_models_and_serializers():
    with pytest.raises(ResearchProgramValidationError):ResearchProgramIdentity("")
    with pytest.raises(ResearchProgramValidationError):ResearchProgramPolicy(enabled=1)
    with pytest.raises(ResearchProgramValidationError):ResearchProgramPolicy(fail_fast=0)
    with pytest.raises(ResearchProgramValidationError):ResearchProgramSummary(1,1,1,1,0)
    with pytest.raises(ResearchProgramValidationError):ResearchProgramSummary(-1,0,0,0,0)
    req=request(1)
    with pytest.raises(ResearchProgramValidationError):replace(req,requested_at=datetime.now(),completed_at=datetime.now())
    with pytest.raises(ResearchProgramValidationError):replace(req,completed_at=req.requested_at-timedelta(seconds=1))
    with pytest.raises(ResearchProgramValidationError):replace(req,studies=[])
    with pytest.raises(ResearchProgramValidationError):replace(req.studies[0],study_request=object())
    with pytest.raises(ResearchProgramValidationError):ResearchProgramStudyRecord(-1,req.studies[0].identity,ResearchProgramStudyStatus.COMPLETED,req.studies[0].study_request,None)
    valid=runtime()[0].run(req)
    with pytest.raises(ResearchProgramValidationError):replace(valid.studies[0],study_result=object())
    with pytest.raises(ResearchProgramValidationError):replace(valid.studies[0],index=True)
    with pytest.raises(ResearchProgramValidationError):replace(valid,studies=(replace(valid.studies[0],index=1),))
    failed=runtime(Executor(callback=lambda ignored:object()))[0].run(req);assert serialize_result(failed)["studies"][0]["study_result"] is None
    for serializer in (serialize_policy,serialize_identity,serialize_study_identity,serialize_study_request,serialize_request,serialize_criteria,serialize_study_record,serialize_summary,serialize_result):
        with pytest.raises(ResearchProgramSerializationError):serializer({})
def test_public_exports():
    import app.research_program as package
    expected=("ResearchProgramRuntime","ResearchStudyExecutor","ResearchProgramStatus","ResearchProgramStudyStatus","ResearchProgramPolicy","ResearchProgramIdentity","ResearchProgramStudyIdentity","ResearchProgramStudyRequest","ResearchProgramRequest","ResearchProgramCriteriaResult","ResearchProgramStudyRecord","ResearchProgramSummary","ResearchProgramResult","serialize_request","serialize_result","validate_request")
    assert all(name in package.__all__ and hasattr(package,name) for name in expected)
