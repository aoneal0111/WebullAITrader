from dataclasses import replace
from datetime import timedelta
from app.backtest_report import BacktestReportPolicy,BacktestReportRuntime
from app.backtest_suite import *
from tests.backtest_run.helpers import request as base_request,runtime as run_runtime
def run_request(index):
    req=base_request(1);run_id=f"run-{index}"
    replay=replace(req.replay_request,identity=replace(req.replay_request.identity,run_id=run_id))
    journal=replace(req.journal_input,identity=replace(req.journal_input.identity,source_run_id=run_id))
    return replace(req,identity=replace(req.identity,run_id=run_id),replay_request=replay,journal_input=journal)
def item(index):return BacktestSuiteItemRequest(BacktestSuiteItemIdentity(f"item-{index}",f"run-{index}",f"report-{index}"),run_request(index),BacktestReportPolicy(),run_request(index).completed_at)
def request(count=3,enabled=True,fail_fast=False):
    items=tuple(item(i) for i in range(count));at=items[0].run_request.requested_at if items else base_request(0).requested_at
    return BacktestSuiteRequest(BacktestSuiteIdentity("suite-1"),items,BacktestSuitePolicy(enabled,fail_fast),at,at+timedelta(hours=10))
class Runs:
    def __init__(self,errors=None,callback=None):self.errors=errors or {};self.callback=callback;self.calls=[]
    def run(self,req):
        self.calls.append(req)
        if req.identity.run_id in self.errors:raise self.errors[req.identity.run_id]
        return self.callback(req) if self.callback else run_runtime()[0].run(req)
class Reports:
    def __init__(self,errors=None,callback=None):self.errors=errors or {};self.callback=callback;self.calls=[];self.runtime=BacktestReportRuntime()
    def create(self,req):
        self.calls.append(req)
        if req.identity.report_id in self.errors:raise self.errors[req.identity.report_id]
        return self.callback(req) if self.callback else self.runtime.create(req)
def runtime(runs=None,reports=None):
    runs=runs or Runs();reports=reports or Reports();return BacktestSuiteRuntime(runs,reports),runs,reports
