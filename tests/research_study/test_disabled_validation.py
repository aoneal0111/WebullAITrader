from dataclasses import replace
import pytest
from app.research_study import *
from tests.research_study.helpers import request,runtime
def test_disabled_and_empty_make_zero_calls_and_preserve_values():
    for req,status in ((request(2,enabled=False),ResearchStudyStatus.DISABLED),(request(0),ResearchStudyStatus.EMPTY)):
        engine,executor=runtime();result=engine.run(req)
        assert result.status is status and result.summary==ResearchStudySummary(0,0,0,0,0) and executor.calls==[]
        assert result.identity is req.identity and result.requested_at is req.requested_at and result.completed_at is req.completed_at
def test_duplicates_and_mismatch_reject_before_calls():
    req=request(2)
    duplicate_entry=replace(req,campaigns=(req.campaigns[0],replace(req.campaigns[1],identity=replace(req.campaigns[1].identity,campaign_entry_id=req.campaigns[0].identity.campaign_entry_id))))
    duplicate_child=replace(req,campaigns=(req.campaigns[0],replace(req.campaigns[1],identity=replace(req.campaigns[1].identity,campaign_id=req.campaigns[0].identity.campaign_id),campaign_request=req.campaigns[0].campaign_request)))
    mismatch=replace(req,campaigns=(replace(req.campaigns[0],identity=replace(req.campaigns[0].identity,campaign_id="other")),req.campaigns[1]))
    for bad in (duplicate_entry,duplicate_child,mismatch):
        engine,executor=runtime();result=engine.run(bad);assert result.status is ResearchStudyStatus.REJECTED and executor.calls==[] and result.campaigns==()
def test_validation_errors_follow_original_campaign_order():
    req=request(3)
    duplicate=replace(req,campaigns=(req.campaigns[0],replace(req.campaigns[1],identity=replace(req.campaigns[1].identity,campaign_entry_id=req.campaigns[0].identity.campaign_entry_id)),replace(req.campaigns[2],identity=replace(req.campaigns[2].identity,campaign_id=req.campaigns[0].identity.campaign_id),campaign_request=req.campaigns[0].campaign_request)))
    engine,executor=runtime();result=engine.run(duplicate)
    assert result.errors==(f"duplicate campaign entry ID at campaign entry {req.campaigns[0].identity.campaign_entry_id}",f"duplicate campaign ID at campaign entry {req.campaigns[2].identity.campaign_entry_id}")
    assert executor.calls==[]
def test_invalid_dependency_and_request():
    with pytest.raises(ResearchStudyDependencyError):ResearchStudyRuntime(None)
    with pytest.raises(ResearchStudyDependencyError):ResearchStudyRuntime(type("Executor",(),{"run":lambda self,req:None}))
    with pytest.raises(ResearchStudyValidationError):runtime()[0].run(object())
