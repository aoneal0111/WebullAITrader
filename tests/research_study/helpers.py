from dataclasses import replace
from datetime import timedelta
from app.research_study import *
from tests.research_campaign.helpers import request as campaign_request,runtime as campaign_runtime
def campaign(index):
    req=campaign_request(1);return replace(req,identity=replace(req.identity,campaign_id=f"campaign-{index}"))
def entry(index):return ResearchStudyCampaignRequest(ResearchStudyCampaignIdentity(f"campaign-entry-{index}",f"campaign-{index}"),campaign(index))
def request(count=3,enabled=True,fail_fast=False):
    campaigns=tuple(entry(i) for i in range(count));at=campaign_request(0).requested_at
    return ResearchStudyRequest(ResearchStudyIdentity("study-1"),campaigns,ResearchStudyPolicy(enabled,fail_fast),at,at+timedelta(days=4))
class Executor:
    def __init__(self,errors=None,callback=None):self.errors=errors or {};self.callback=callback;self.calls=[]
    def run(self,req):
        self.calls.append(req)
        if req.identity.campaign_id in self.errors:raise self.errors[req.identity.campaign_id]
        return self.callback(req) if self.callback else campaign_runtime()[0].run(req)
def runtime(executor=None):
    executor=executor or Executor();return ResearchStudyRuntime(executor),executor
