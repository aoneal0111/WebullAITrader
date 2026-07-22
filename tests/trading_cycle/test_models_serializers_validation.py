from dataclasses import replace
from datetime import datetime,timedelta
import pytest
from app.trading_cycle import *
from tests.trading_cycle.helpers import build_request,builder

def test_all_enum_values():
    assert [x.value for x in TradingCycleMode]==["PAPER","BACKTEST","LIVE"]
    assert len(TradingCycleOutcome)==9 and len(TradingCycleStage)==7 and len(TradingCycleStageStatus)==5

def test_request_cycle_result_round_trips_and_stable_serialization():
    req=build_request();assert TradingCycleBuildRequest.from_dict(req.to_dict())==req
    result=builder().build(req);encoded=result.to_dict();assert TradingCycleBuildResult.from_dict(encoded)==result and encoded==result.to_dict()

def test_invalid_timing():
    req=build_request()
    with pytest.raises(TradingCycleValidationError):replace(req,completed_at=req.started_at-timedelta(seconds=1))
    with pytest.raises(TradingCycleValidationError):replace(req,started_at=datetime(2026,1,1))

def test_orchestrator_request_identity_mismatch_is_structural_zero_call():
    req=build_request();bad=replace(req.orchestrator_result,request_id="wrong");evaluator=type("Never",(),{"calls":[],"evaluate":lambda self,r:self.calls.append(r)})()
    with pytest.raises(TradingCycleValidationError):builder(evaluator).build(replace(req,orchestrator_result=bad))
    assert evaluator.calls==[]

def test_account_identity_mismatch_is_structural_zero_call():
    from app.paper_trading import PaperTradingAccount
    req=build_request();wrong=PaperTradingAccount("wrong","1","1",(),(),(),"0","0","0","1");evaluator=type("Never",(),{"calls":[],"evaluate":lambda self,r:self.calls.append(r)})()
    with pytest.raises(TradingCycleValidationError):builder(evaluator).build(replace(req,original_account=wrong))
    assert evaluator.calls==[]

def test_instruction_quantity_mismatch():
    req=build_request();plan=req.orchestrator_result.execution_plan_result;ins=plan.plan.instructions[0]
    from app.execution_planner import ExecutionPlan
    bad_plan=replace(plan,plan=ExecutionPlan(plan.plan.request_id,(replace(ins,quantity=ins.quantity+1),)))
    o=replace(req.orchestrator_result,execution_plan_result=bad_plan,paper_execution_result=None,resulting_account=req.original_account)
    with pytest.raises(TradingCycleValidationError):builder().build(replace(req,orchestrator_result=o))

def test_missing_optional_artifacts_supported():
    req=build_request();empty=replace(req,orchestrator_result=None,strategy_result=None,risk_result=None,execution_plan_result=None,paper_execution_result=None,resulting_account=None,metadata={"failed_stage":"STRATEGY"})
    cycle=builder().build(empty).cycle
    assert cycle.identity.symbol is None and cycle.decision_trace.strategy_signal is None and cycle.metrics.ending_cash is None

def test_policy_capabilities_and_serialization():
    policy=TradingCyclePolicy(enabled=True,include_metrics=False,include_decision_trace=False,include_diagnostics=False,include_stage_records=False)
    assert TradingCyclePolicy.from_dict(policy.to_dict())==policy
    cycle=TradingCycleBuilder(policy).build(build_request()).cycle
    assert cycle.metrics is None and cycle.decision_trace is None and cycle.diagnostics is None and cycle.stage_records==()
