from dataclasses import replace
from app.backtest_suite import BacktestSuiteStatus
from app.parameter_sweep import *
from tests.parameter_sweep.helpers import Executor,request,runtime
def test_three_cases_exact_order_calls_and_continuity():
    req=request(3);engine,executor=runtime();result=engine.run(req)
    assert result.status is ParameterSweepStatus.COMPLETED and executor.calls==[x.suite_request for x in req.cases]
    assert all(executor.calls[i] is req.cases[i].suite_request and result.cases[i].suite_request is req.cases[i].suite_request for i in range(3))
    assert all(x.suite_result is not None for x in result.cases) and result.summary==ParameterSweepSummary(3,3,3,0,0)
def test_all_child_suite_statuses_are_completed_orchestration():
    from tests.backtest_suite.helpers import runtime as sruntime
    for status in BacktestSuiteStatus:
        executor=Executor(callback=lambda req,status=status:replace(sruntime()[0].run(req),status=status))
        result=runtime(executor)[0].run(request(1));assert result.cases[0].status is ParameterSweepCaseStatus.COMPLETED and result.status is ParameterSweepStatus.COMPLETED
def test_repeated_equal_requests_are_deterministic():
    req=request(2);a=runtime()[0].run(req);b=runtime()[0].run(req);assert a==b and a.to_dict()==b.to_dict()
