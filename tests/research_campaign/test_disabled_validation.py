from dataclasses import replace
import pytest
from app.research_campaign import *
from tests.research_campaign.helpers import request,runtime
def test_disabled_and_empty_make_zero_calls():
    for req,status in ((request(2,enabled=False),ResearchCampaignStatus.DISABLED),(request(0),ResearchCampaignStatus.EMPTY)):
        engine,executor=runtime();result=engine.run(req)
        assert result.status is status and result.summary==ResearchCampaignSummary(0,0,0,0,0) and executor.calls==[]
        assert result.identity is req.identity and result.requested_at is req.requested_at and result.completed_at is req.completed_at
def test_mismatch_and_duplicate_ids_reject_before_calls():
    req=request(2)
    duplicate_entry=replace(req,experiments=(req.experiments[0],replace(req.experiments[1],identity=replace(req.experiments[1].identity,experiment_entry_id=req.experiments[0].identity.experiment_entry_id))))
    duplicate_child=replace(req,experiments=(req.experiments[0],replace(req.experiments[1],identity=replace(req.experiments[1].identity,experiment_id=req.experiments[0].identity.experiment_id),experiment_request=req.experiments[0].experiment_request)))
    mismatch=replace(req,experiments=(replace(req.experiments[0],identity=replace(req.experiments[0].identity,experiment_id="other")),req.experiments[1]))
    for bad in (duplicate_entry,duplicate_child,mismatch):
        engine,executor=runtime();result=engine.run(bad)
        assert result.status is ResearchCampaignStatus.REJECTED and result.experiments==() and executor.calls==[]
def test_validation_error_order_follows_original_entry_order():
    req=request(3)
    duplicate=replace(req,experiments=(req.experiments[0],replace(req.experiments[1],identity=replace(req.experiments[1].identity,experiment_entry_id=req.experiments[0].identity.experiment_entry_id)),replace(req.experiments[2],identity=replace(req.experiments[2].identity,experiment_id=req.experiments[0].identity.experiment_id),experiment_request=req.experiments[0].experiment_request)))
    result=runtime()[0].run(duplicate)
    assert result.errors==(f"duplicate experiment entry ID at experiment entry {req.experiments[0].identity.experiment_entry_id}",f"duplicate experiment ID at experiment entry {req.experiments[2].identity.experiment_entry_id}")
def test_invalid_dependency_and_request_type():
    with pytest.raises(ResearchCampaignDependencyError):ResearchCampaignRuntime(None)
    with pytest.raises(ResearchCampaignDependencyError):ResearchCampaignRuntime(type("ExecutorClass",(),{"run":lambda self,request:None}))
    with pytest.raises(ResearchCampaignValidationError):runtime()[0].run(object())
    with pytest.raises(ResearchCampaignValidationError):ResearchCampaignPolicy(fail_fast=0)
