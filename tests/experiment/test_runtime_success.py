from dataclasses import replace
from app.experiment import *
from app.parameter_sweep import ParameterSweepStatus
from tests.experiment.helpers import Executor,request,runtime
from tests.parameter_sweep.helpers import runtime as parameter_sweep_runtime
def test_exact_order_calls_and_object_continuity():
    req=request(3);engine,executor=runtime();result=engine.run(req)
    assert result.status is ExperimentStatus.COMPLETED and executor.calls==[x.parameter_sweep_request for x in req.sweeps]
    assert all(executor.calls[i] is req.sweeps[i].parameter_sweep_request and result.sweeps[i].parameter_sweep_request is req.sweeps[i].parameter_sweep_request for i in range(3))
    assert all(result.sweeps[i].parameter_sweep_result is not None for i in range(3))
    assert result.summary==ExperimentSummary(3,3,3,0,0)
    assert result.identity is req.identity and result.requested_at is req.requested_at and result.completed_at is req.completed_at
    assert all(result.sweeps[i].identity is req.sweeps[i].identity for i in range(3))
def test_all_child_statuses_are_successful_orchestration():
    for status in ParameterSweepStatus:
        executor=Executor(callback=lambda req,status=status:replace(runtime()[0]._executor.run(req),status=status))
        result=runtime(executor)[0].run(request(1))
        assert result.sweeps[0].status is ExperimentSweepStatus.COMPLETED and result.status is ExperimentStatus.COMPLETED
def test_repeated_equal_requests_are_deterministic():
    req=request(2);a=runtime()[0].run(req);b=runtime()[0].run(req)
    assert a==b and serialize_result(a)==serialize_result(b)
def test_exact_returned_parameter_sweep_result_is_preserved():
    req=request(1);child=parameter_sweep_runtime()[0].run(req.sweeps[0].parameter_sweep_request)
    result=runtime(Executor(callback=lambda ignored:child))[0].run(req)
    assert result.sweeps[0].parameter_sweep_result is child
