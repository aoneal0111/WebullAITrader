from dataclasses import replace
from datetime import timedelta
from app.research_portfolio import *
from tests.research_program.helpers import request as program_request,runtime as program_runtime
def program(index):
    req=program_request(1);return replace(req,identity=replace(req.identity,program_id=f"program-{index}"))
def entry(index):return ResearchPortfolioProgramRequest(ResearchPortfolioProgramIdentity(f"program-entry-{index}",f"program-{index}"),program(index))
def request(count=3,enabled=True,fail_fast=False):
    programs=tuple(entry(i) for i in range(count));at=program_request(0).requested_at
    return ResearchPortfolioRequest(ResearchPortfolioIdentity("portfolio-1"),programs,ResearchPortfolioPolicy(enabled,fail_fast),at,at+timedelta(days=6))
class Executor:
    def __init__(self,errors=None,callback=None):self.errors=errors or {};self.callback=callback;self.calls=[]
    def run(self,req):
        self.calls.append(req)
        if req.identity.program_id in self.errors:raise self.errors[req.identity.program_id]
        return self.callback(req) if self.callback else program_runtime()[0].run(req)
def runtime(executor=None):
    executor=executor or Executor();return ResearchPortfolioRuntime(executor),executor
