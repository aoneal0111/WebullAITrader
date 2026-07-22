from dataclasses import replace
from datetime import timedelta
from app.parameter_sweep import *
from tests.backtest_suite.helpers import request as suite_request,runtime as suite_runtime
def suite(index):return replace(suite_request(1),identity=replace(suite_request(1).identity,suite_id=f"suite-{index}"))
def case(index):return ParameterSweepCaseRequest(ParameterSweepCaseIdentity(f"case-{index}",f"suite-{index}"),suite(index))
def request(count=3,enabled=True,fail_fast=False):
    cases=tuple(case(i) for i in range(count));at=suite_request(0).requested_at
    return ParameterSweepRequest(ParameterSweepIdentity("sweep-1"),cases,ParameterSweepPolicy(enabled,fail_fast),at,at+timedelta(hours=20))
class Executor:
    def __init__(self,errors=None,callback=None):self.errors=errors or {};self.callback=callback;self.calls=[]
    def run(self,req):
        self.calls.append(req)
        if req.identity.suite_id in self.errors:raise self.errors[req.identity.suite_id]
        if self.callback:return self.callback(req)
        return suite_runtime()[0].run(req)
def runtime(executor=None):
    executor=executor or Executor();return ParameterSweepRuntime(executor),executor
