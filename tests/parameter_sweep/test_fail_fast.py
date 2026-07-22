from app.parameter_sweep import *
from tests.parameter_sweep.helpers import Executor,request,runtime
def test_fail_fast_stops_and_emits_skipped_records():
    req=request(3,fail_fast=True);engine,executor=runtime(Executor(errors={"suite-1":RuntimeError()}));result=engine.run(req)
    assert len(executor.calls)==2 and tuple(x.status for x in result.cases)==(ParameterSweepCaseStatus.COMPLETED,ParameterSweepCaseStatus.SUITE_FAILED,ParameterSweepCaseStatus.SKIPPED)
    assert result.cases[2].suite_request is req.cases[2].suite_request and result.cases[2].suite_result is None
    assert result.cases[2].message=="Skipped because fail-fast policy stopped the sweep." and result.status is ParameterSweepStatus.PARTIALLY_COMPLETED
def test_fail_fast_first_failure_is_failed():
    result=runtime(Executor(errors={"suite-0":RuntimeError()}))[0].run(request(3,fail_fast=True));assert result.status is ParameterSweepStatus.FAILED and result.summary.skipped_cases==2
