from app.experiment import ExperimentStatus,ExperimentSweepStatus
from tests.experiment.helpers import Executor,request,runtime
def test_same_runtime_has_no_cross_call_state():
    engine,_=runtime();a=engine.run(request(2));b=engine.run(request(0));c=engine.run(request(1,enabled=False))
    assert (a.status,b.status,c.status)==(ExperimentStatus.COMPLETED,ExperimentStatus.EMPTY,ExperimentStatus.DISABLED)
    assert len(a.sweeps)==2 and b.sweeps==c.sweeps==()
    assert a.sweeps is not b.sweeps and a.summary is not b.summary
def test_failure_and_fail_fast_state_reset_each_run():
    executor=Executor(errors={"sweep-0":RuntimeError()});engine,_=runtime(executor)
    failed=engine.run(request(2,fail_fast=True));executor.errors={};completed=engine.run(request(2,fail_fast=True))
    assert tuple(x.status for x in failed.sweeps)==(ExperimentSweepStatus.SWEEP_FAILED,ExperimentSweepStatus.SKIPPED)
    assert completed.status is ExperimentStatus.COMPLETED and all(x.status is ExperimentSweepStatus.COMPLETED for x in completed.sweeps)
