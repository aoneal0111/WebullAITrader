from dataclasses import replace
from app.backtest_suite import *
from tests.backtest_suite.helpers import request,runtime
def test_disabled_zero_calls_empty_records():
    req=request(2,enabled=False);engine,runs,reports=runtime();result=engine.run(req)
    assert result.status is BacktestSuiteStatus.DISABLED and result.items==() and result.summary==BacktestSuiteSummary(0,0,0,0,0)
    assert runs.calls==reports.calls==[]
def test_empty_suite():
    req=request(0);engine,runs,reports=runtime();result=engine.run(req)
    assert result.status is BacktestSuiteStatus.EMPTY and result.summary==BacktestSuiteSummary(0,0,0,0,0) and runs.calls==reports.calls==[]
def test_duplicate_and_mismatched_ids_rejected_zero_calls():
    req=request(2);duplicate=replace(req,items=(req.items[0],replace(req.items[1],identity=replace(req.items[1].identity,item_id=req.items[0].identity.item_id))))
    mismatch=replace(req,items=(replace(req.items[0],identity=replace(req.items[0].identity,run_id="other")),req.items[1]))
    for bad in (duplicate,mismatch):
        engine,runs,reports=runtime();result=engine.run(bad);assert result.status is BacktestSuiteStatus.REJECTED and runs.calls==reports.calls==[]
