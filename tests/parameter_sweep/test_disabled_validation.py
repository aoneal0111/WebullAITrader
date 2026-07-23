from dataclasses import replace
import pytest
from app.parameter_sweep import *
from tests.parameter_sweep.helpers import request,runtime
def test_disabled_zero_calls_and_zero_summary():
    req=request(2,enabled=False);engine,executor=runtime();result=engine.run(req)
    assert result.status is ParameterSweepStatus.DISABLED and result.cases==() and result.summary==ParameterSweepSummary(0,0,0,0,0) and executor.calls==[]
def test_empty_sweep():
    engine,executor=runtime();result=engine.run(request(0));assert result.status is ParameterSweepStatus.EMPTY and result.summary==ParameterSweepSummary(0,0,0,0,0) and executor.calls==[]
def test_duplicate_and_identity_mismatch_rejected_zero_calls():
    req=request(2);duplicate=replace(req,cases=(req.cases[0],replace(req.cases[1],identity=replace(req.cases[1].identity,case_id=req.cases[0].identity.case_id))))
    mismatch=replace(req,cases=(replace(req.cases[0],identity=replace(req.cases[0].identity,suite_id="other")),req.cases[1]))
    for bad in (duplicate,mismatch):
        engine,executor=runtime();result=engine.run(bad);assert result.status is ParameterSweepStatus.REJECTED and executor.calls==[] and result.cases==()
def test_wrong_dependency_and_request_type():
    with pytest.raises(ParameterSweepDependencyError):ParameterSweepRuntime(None)
    with pytest.raises(ParameterSweepValidationError):runtime()[0].run(object())
