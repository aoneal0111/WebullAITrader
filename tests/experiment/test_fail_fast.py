from app.experiment import *
from tests.experiment.helpers import Executor,request,runtime
def test_fail_fast_stops_and_records_skips():
    req=request(3,fail_fast=True);engine,executor=runtime(Executor(errors={"sweep-1":RuntimeError()}));result=engine.run(req)
    assert len(executor.calls)==2
    assert tuple(x.status for x in result.sweeps)==(ExperimentSweepStatus.COMPLETED,ExperimentSweepStatus.SWEEP_FAILED,ExperimentSweepStatus.SKIPPED)
    assert result.sweeps[2].parameter_sweep_request is req.sweeps[2].parameter_sweep_request and result.status is ExperimentStatus.PARTIALLY_COMPLETED
def test_first_failure_means_failed():
    result=runtime(Executor(errors={"sweep-0":RuntimeError()}))[0].run(request(3,fail_fast=True))
    assert result.status is ExperimentStatus.FAILED and result.summary.skipped_sweeps==2
