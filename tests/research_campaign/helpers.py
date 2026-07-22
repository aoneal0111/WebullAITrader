from dataclasses import replace
from datetime import timedelta
from app.research_campaign import *
from tests.experiment.helpers import request as experiment_request,runtime as experiment_runtime
def experiment(index):
    req=experiment_request(1);return replace(req,identity=replace(req.identity,experiment_id=f"experiment-{index}"))
def entry(index):return ResearchCampaignExperimentRequest(ResearchCampaignExperimentIdentity(f"experiment-entry-{index}",f"experiment-{index}"),experiment(index))
def request(count=3,enabled=True,fail_fast=False):
    experiments=tuple(entry(i) for i in range(count));at=experiment_request(0).requested_at
    return ResearchCampaignRequest(ResearchCampaignIdentity("campaign-1"),experiments,ResearchCampaignPolicy(enabled,fail_fast),at,at+timedelta(days=3))
class Executor:
    def __init__(self,errors=None,callback=None):self.errors=errors or {};self.callback=callback;self.calls=[]
    def run(self,req):
        self.calls.append(req)
        if req.identity.experiment_id in self.errors:raise self.errors[req.identity.experiment_id]
        return self.callback(req) if self.callback else experiment_runtime()[0].run(req)
def runtime(executor=None):
    executor=executor or Executor();return ResearchCampaignRuntime(executor),executor
