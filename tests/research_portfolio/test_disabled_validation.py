from dataclasses import replace
import pytest
from app.research_portfolio import *
from tests.research_portfolio.helpers import request,runtime
def test_disabled_and_empty():
    for req,status in ((request(2,enabled=False),ResearchPortfolioStatus.DISABLED),(request(0),ResearchPortfolioStatus.EMPTY)):
        engine,executor=runtime();result=engine.run(req)
        assert result.status is status and result.summary==ResearchPortfolioSummary(0,0,0,0,0) and executor.calls==[]
        assert result.identity is req.identity and result.requested_at is req.requested_at and result.completed_at is req.completed_at
def test_duplicates_mismatch_and_error_order():
    req=request(3)
    duplicate=replace(req,programs=(req.programs[0],replace(req.programs[1],identity=replace(req.programs[1].identity,program_entry_id=req.programs[0].identity.program_entry_id)),replace(req.programs[2],identity=replace(req.programs[2].identity,program_id=req.programs[0].identity.program_id),program_request=req.programs[0].program_request)))
    engine,executor=runtime();result=engine.run(duplicate)
    assert result.status is ResearchPortfolioStatus.REJECTED and result.programs==() and executor.calls==[]
    assert result.errors==(f"duplicate program entry ID at program entry {req.programs[0].identity.program_entry_id}",f"duplicate program ID at program entry {req.programs[2].identity.program_entry_id}")
    mismatch=replace(req,programs=(replace(req.programs[0],identity=replace(req.programs[0].identity,program_id="other")),)+req.programs[1:])
    assert runtime()[0].run(mismatch).status is ResearchPortfolioStatus.REJECTED
def test_invalid_dependency_and_request():
    with pytest.raises(ResearchPortfolioDependencyError):ResearchPortfolioRuntime(None)
    with pytest.raises(ResearchPortfolioDependencyError):ResearchPortfolioRuntime(type("Executor",(),{"run":lambda self,req:None}))
    with pytest.raises(ResearchPortfolioValidationError):runtime()[0].run(object())
def test_invalid_structural_members_are_rejected_safely():
    req=request(1)
    invalid=(lambda:ResearchPortfolioIdentity(" "),lambda:replace(req,identity=object()),lambda:replace(req,policy=object()),lambda:replace(req,programs=[]),lambda:replace(req,programs=(object(),)),lambda:replace(req.programs[0],identity=object()),lambda:replace(req.programs[0].identity,program_entry_id=""),lambda:replace(req.programs[0].identity,program_id=""),lambda:replace(req.programs[0],program_request=object()))
    for construct in invalid:
        with pytest.raises(ResearchPortfolioValidationError):construct()
