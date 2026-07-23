from dataclasses import FrozenInstanceError,replace
from datetime import datetime,timedelta
import pytest
from app.research_portfolio import *
from tests.research_portfolio.helpers import Executor,request,runtime
def test_models_policy_immutability_and_serialization():
    assert len(ResearchPortfolioStatus)==6 and len(ResearchPortfolioProgramStatus)==3
    assert ResearchPortfolioPolicy()==ResearchPortfolioPolicy.from_dict(ResearchPortfolioPolicy().to_dict())
    req=request(2);result=runtime()[0].run(req);data=serialize_result(result)
    assert data==serialize_result(result)==result.to_dict() and serialize_request(req)==req.to_dict()
    assert data["status"]=="COMPLETED" and data["requested_at"]==req.requested_at.isoformat()
    assert data["programs"][0]["program_request"]==req.programs[0].program_request.to_dict() and data["programs"][0]["program_result"]==result.programs[0].program_result.to_dict()
    for model,field,value in ((req.identity,"portfolio_id","x"),(req.programs[0].identity,"program_entry_id","x"),(req,"programs",()),(result.programs[0],"index",2),(result.summary,"total_programs",2),(result,"status",ResearchPortfolioStatus.FAILED)):
        with pytest.raises(FrozenInstanceError):setattr(model,field,value)
def test_invalid_models_and_serializers():
    with pytest.raises(ResearchPortfolioValidationError):ResearchPortfolioIdentity("")
    with pytest.raises(ResearchPortfolioValidationError):ResearchPortfolioPolicy(enabled=1)
    with pytest.raises(ResearchPortfolioValidationError):ResearchPortfolioPolicy(fail_fast=0)
    with pytest.raises(ResearchPortfolioValidationError):ResearchPortfolioSummary(1,1,1,1,0)
    with pytest.raises(ResearchPortfolioValidationError):ResearchPortfolioSummary(-1,0,0,0,0)
    req=request(1)
    assert req.identity.portfolio_id=="portfolio-1" and req.programs[0].program_request is req.programs[0].program_request
    with pytest.raises(ResearchPortfolioValidationError):replace(req,requested_at=datetime.now(),completed_at=datetime.now())
    with pytest.raises(ResearchPortfolioValidationError):replace(req,completed_at=req.requested_at-timedelta(seconds=1))
    with pytest.raises(ResearchPortfolioValidationError):replace(req,programs=[])
    with pytest.raises(ResearchPortfolioValidationError):replace(req.programs[0],program_request=object())
    with pytest.raises(ResearchPortfolioValidationError):ResearchPortfolioProgramRecord(-1,req.programs[0].identity,ResearchPortfolioProgramStatus.COMPLETED,req.programs[0].program_request,None)
    valid=runtime()[0].run(req)
    with pytest.raises(ResearchPortfolioValidationError):replace(valid.programs[0],program_result=object())
    with pytest.raises(ResearchPortfolioValidationError):replace(valid.programs[0],index=True)
    with pytest.raises(ResearchPortfolioValidationError):replace(valid,programs=(replace(valid.programs[0],index=1),))
    failed=runtime(Executor(callback=lambda ignored:object()))[0].run(req);assert serialize_result(failed)["programs"][0]["program_result"] is None
    for serializer in (serialize_policy,serialize_identity,serialize_program_identity,serialize_program_request,serialize_request,serialize_criteria,serialize_program_record,serialize_summary,serialize_result):
        for invalid in ({},"bad",None):
            with pytest.raises(ResearchPortfolioSerializationError):serializer(invalid)
def test_public_exports():
    import app.research_portfolio as package
    expected=("ResearchPortfolioRuntime","ResearchProgramExecutor","ResearchPortfolioStatus","ResearchPortfolioProgramStatus","ResearchPortfolioPolicy","ResearchPortfolioIdentity","ResearchPortfolioProgramIdentity","ResearchPortfolioProgramRequest","ResearchPortfolioRequest","ResearchPortfolioCriteriaResult","ResearchPortfolioProgramRecord","ResearchPortfolioSummary","ResearchPortfolioResult","serialize_request","serialize_result","validate_request")
    assert all(name in package.__all__ and hasattr(package,name) for name in expected)
