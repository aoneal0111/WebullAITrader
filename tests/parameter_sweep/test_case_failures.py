from app.parameter_sweep import *
from tests.parameter_sweep.helpers import Executor,request,runtime
def test_continue_after_suite_failure():
    engine,executor=runtime(Executor(errors={"suite-1":ValueError("secret")}));result=engine.run(request(3))
    assert tuple(x.status for x in result.cases)==(ParameterSweepCaseStatus.COMPLETED,ParameterSweepCaseStatus.SUITE_FAILED,ParameterSweepCaseStatus.COMPLETED)
    assert len(executor.calls)==3 and result.status is ParameterSweepStatus.PARTIALLY_COMPLETED and result.summary.failed_cases==1
    assert result.cases[1].error_type=="ValueError" and result.cases[1].message=="Backtest suite invocation failed."
def test_all_failures_is_failed():
    result=runtime(Executor(errors={"suite-0":RuntimeError(),"suite-1":RuntimeError()}))[0].run(request(2));assert result.status is ParameterSweepStatus.FAILED
def test_invalid_return_is_deterministic_failure():
    result=runtime(Executor(callback=lambda req:object()))[0].run(request(1));record=result.cases[0]
    assert record.status is ParameterSweepCaseStatus.SUITE_FAILED and record.suite_result is None and record.error_type=="InvalidBacktestSuiteResult" and result.status is ParameterSweepStatus.FAILED
