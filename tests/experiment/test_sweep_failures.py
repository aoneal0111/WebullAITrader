from app.experiment import *
from tests.experiment.helpers import Executor,request,runtime
def test_continue_after_failure_and_sanitize_exception():
    result=runtime(Executor(errors={"sweep-1":ValueError("secret")}))[0].run(request(3))
    assert tuple(x.status for x in result.sweeps)==(ExperimentSweepStatus.COMPLETED,ExperimentSweepStatus.SWEEP_FAILED,ExperimentSweepStatus.COMPLETED)
    assert result.status is ExperimentStatus.PARTIALLY_COMPLETED and result.sweeps[1].error_type=="ValueError" and "secret" not in result.to_dict().__str__()
def test_invalid_return_and_all_failure_status():
    result=runtime(Executor(callback=lambda req:object()))[0].run(request(2))
    assert result.status is ExperimentStatus.FAILED and all(x.error_type=="InvalidParameterSweepResult" for x in result.sweeps)
