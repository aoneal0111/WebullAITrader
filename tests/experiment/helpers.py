from dataclasses import replace
from datetime import timedelta
from app.experiment import *
from tests.parameter_sweep.helpers import request as sweep_request,runtime as sweep_runtime
def sweep(index):
    req=sweep_request(1);return replace(req,identity=replace(req.identity,sweep_id=f"sweep-{index}"))
def entry(index):return ExperimentSweepRequest(ExperimentSweepIdentity(f"experiment-sweep-{index}",f"sweep-{index}"),sweep(index))
def request(count=3,enabled=True,fail_fast=False):
    sweeps=tuple(entry(i) for i in range(count));at=sweep_request(0).requested_at
    return ExperimentRequest(ExperimentIdentity("experiment-1"),sweeps,ExperimentPolicy(enabled,fail_fast),at,at+timedelta(days=2))
class Executor:
    def __init__(self,errors=None,callback=None):self.errors=errors or {};self.callback=callback;self.calls=[]
    def run(self,req):
        self.calls.append(req)
        if req.identity.sweep_id in self.errors:raise self.errors[req.identity.sweep_id]
        return self.callback(req) if self.callback else sweep_runtime()[0].run(req)
def runtime(executor=None):
    executor=executor or Executor();return ExperimentRuntime(executor),executor
