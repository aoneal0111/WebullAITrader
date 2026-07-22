from dataclasses import replace
import pytest
from app.experiment import *
from tests.experiment.helpers import request,runtime
def test_disabled_and_empty_make_zero_calls():
    for req,status in ((request(2,enabled=False),ExperimentStatus.DISABLED),(request(0),ExperimentStatus.EMPTY)):
        engine,executor=runtime();result=engine.run(req)
        assert result.status is status and result.summary==ExperimentSummary(0,0,0,0,0) and executor.calls==[]
def test_duplicate_and_mismatch_are_rejected():
    req=request(2)
    duplicate=replace(req,sweeps=(req.sweeps[0],replace(req.sweeps[1],identity=replace(req.sweeps[1].identity,sweep_entry_id=req.sweeps[0].identity.sweep_entry_id))))
    mismatch=replace(req,sweeps=(replace(req.sweeps[0],identity=replace(req.sweeps[0].identity,parameter_sweep_id="other")),req.sweeps[1]))
    for bad in (duplicate,mismatch):
        engine,executor=runtime();result=engine.run(bad);assert result.status is ExperimentStatus.REJECTED and not executor.calls
def test_duplicate_parameter_sweep_ids_are_rejected_before_execution():
    req=request(2)
    duplicate=replace(req,sweeps=(req.sweeps[0],replace(req.sweeps[1],identity=replace(req.sweeps[1].identity,parameter_sweep_id=req.sweeps[0].identity.parameter_sweep_id),parameter_sweep_request=req.sweeps[0].parameter_sweep_request)))
    engine,executor=runtime();result=engine.run(duplicate)
    assert result.status is ExperimentStatus.REJECTED and executor.calls==[]
def test_wrong_dependency_and_request_type():
    with pytest.raises(ExperimentDependencyError):ExperimentRuntime(None)
    with pytest.raises(ExperimentDependencyError):ExperimentRuntime(type("ExecutorClass",(),{"run":lambda self,request:None}))
    with pytest.raises(ExperimentValidationError):runtime()[0].run(object())
