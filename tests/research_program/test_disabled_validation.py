from dataclasses import replace
import pytest
from app.research_program import *
from tests.research_program.helpers import request,runtime
def test_disabled_and_empty():
    for req,status in ((request(2,enabled=False),ResearchProgramStatus.DISABLED),(request(0),ResearchProgramStatus.EMPTY)):
        engine,executor=runtime();result=engine.run(req)
        assert result.status is status and result.summary==ResearchProgramSummary(0,0,0,0,0) and executor.calls==[]
        assert result.identity is req.identity and result.requested_at is req.requested_at and result.completed_at is req.completed_at
def test_duplicates_mismatch_and_error_order():
    req=request(3)
    duplicate=replace(req,studies=(req.studies[0],replace(req.studies[1],identity=replace(req.studies[1].identity,study_entry_id=req.studies[0].identity.study_entry_id)),replace(req.studies[2],identity=replace(req.studies[2].identity,study_id=req.studies[0].identity.study_id),study_request=req.studies[0].study_request)))
    engine,executor=runtime();result=engine.run(duplicate)
    assert result.status is ResearchProgramStatus.REJECTED and result.studies==() and executor.calls==[]
    assert result.errors==(f"duplicate study entry ID at study entry {req.studies[0].identity.study_entry_id}",f"duplicate study ID at study entry {req.studies[2].identity.study_entry_id}")
    mismatch=replace(req,studies=(replace(req.studies[0],identity=replace(req.studies[0].identity,study_id="other")),)+req.studies[1:])
    assert runtime()[0].run(mismatch).status is ResearchProgramStatus.REJECTED
def test_invalid_dependency_and_request():
    with pytest.raises(ResearchProgramDependencyError):ResearchProgramRuntime(None)
    with pytest.raises(ResearchProgramDependencyError):ResearchProgramRuntime(type("Executor",(),{"run":lambda self,req:None}))
    with pytest.raises(ResearchProgramValidationError):runtime()[0].run(object())
def test_invalid_structural_members_are_rejected_at_construction():
    req=request(1)
    invalid=(lambda:ResearchProgramIdentity(" "),lambda:replace(req,identity=object()),lambda:replace(req,policy=object()),lambda:replace(req,studies=[]),lambda:replace(req,studies=(object(),)),lambda:replace(req.studies[0],identity=object()),lambda:replace(req.studies[0].identity,study_entry_id=""),lambda:replace(req.studies[0].identity,study_id=""),lambda:replace(req.studies[0],study_request=object()))
    for construct in invalid:
        with pytest.raises(ResearchProgramValidationError):construct()
