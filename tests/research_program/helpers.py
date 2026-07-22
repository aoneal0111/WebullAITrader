from dataclasses import replace
from datetime import timedelta
from app.research_program import *
from tests.research_study.helpers import request as study_request,runtime as study_runtime
def study(index):
    req=study_request(1);return replace(req,identity=replace(req.identity,study_id=f"study-{index}"))
def entry(index):return ResearchProgramStudyRequest(ResearchProgramStudyIdentity(f"study-entry-{index}",f"study-{index}"),study(index))
def request(count=3,enabled=True,fail_fast=False):
    studies=tuple(entry(i) for i in range(count));at=study_request(0).requested_at
    return ResearchProgramRequest(ResearchProgramIdentity("program-1"),studies,ResearchProgramPolicy(enabled,fail_fast),at,at+timedelta(days=5))
class Executor:
    def __init__(self,errors=None,callback=None):self.errors=errors or {};self.callback=callback;self.calls=[]
    def run(self,req):
        self.calls.append(req)
        if req.identity.study_id in self.errors:raise self.errors[req.identity.study_id]
        return self.callback(req) if self.callback else study_runtime()[0].run(req)
def runtime(executor=None):
    executor=executor or Executor();return ResearchProgramRuntime(executor),executor
