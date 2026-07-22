from dataclasses import FrozenInstanceError,replace
from decimal import Decimal
import pytest
from app.execution_orchestrator import PaperTradingCycleOutcome
from app.strategy import StrategySignal
from app.trading_cycle import *
from tests.trading_cycle.helpers import Evaluator,build_request,builder

@pytest.mark.parametrize("signal,held",[(StrategySignal.BUY,False),(StrategySignal.SELL,True)])
def test_successful_buy_and_sell(signal,held):
    result=builder().build(build_request(signal=signal,with_position=held));cycle=result.cycle
    assert cycle.outcome is TradingCycleOutcome.EXECUTED and cycle.identity.symbol=="AAPL"
    assert cycle.decision_trace.strategy_signal==signal.value and cycle.decision_trace.filled_quantity==10
    assert all(x.status is TradingCycleStageStatus.COMPLETED for x in cycle.stage_records)

def test_partial_execution_and_fee_trace():
    cycle=builder().build(build_request(partial=True)).cycle
    assert cycle.outcome is TradingCycleOutcome.PARTIALLY_EXECUTED and cycle.decision_trace.filled_quantity==5
    assert cycle.decision_trace.fees==0 and cycle.metrics.total_fees==0

@pytest.mark.parametrize("signal",[StrategySignal.HOLD,StrategySignal.EXIT])
def test_no_action_skips_downstream(signal):
    cycle=builder().build(build_request(signal=signal)).cycle
    assert cycle.outcome is TradingCycleOutcome.NO_ACTION
    statuses={x.stage:x.status for x in cycle.stage_records}
    assert statuses[TradingCycleStage.STRATEGY] is TradingCycleStageStatus.COMPLETED
    assert all(statuses[x] is TradingCycleStageStatus.SKIPPED for x in (TradingCycleStage.RISK,TradingCycleStage.PLANNING,TradingCycleStage.EXECUTION))

@pytest.mark.parametrize("outcome,stage",[(PaperTradingCycleOutcome.STRATEGY_REJECTED,TradingCycleStage.STRATEGY),(PaperTradingCycleOutcome.RISK_REJECTED,TradingCycleStage.RISK),(PaperTradingCycleOutcome.PLANNING_REJECTED,TradingCycleStage.PLANNING),(PaperTradingCycleOutcome.EXECUTION_REJECTED,TradingCycleStage.EXECUTION)])
def test_rejection_mapping_and_stage(outcome,stage):
    req=build_request();o=req.orchestrator_result
    keep_paper=stage is TradingCycleStage.EXECUTION
    altered=replace(o,outcome=outcome,paper_execution_result=o.paper_execution_result if keep_paper else None,resulting_account=o.resulting_account if keep_paper else req.original_account)
    if stage is TradingCycleStage.STRATEGY:altered=replace(altered,risk_result=None,execution_plan_result=None)
    elif stage is TradingCycleStage.RISK:altered=replace(altered,execution_plan_result=None)
    result=builder().build(replace(req,orchestrator_result=altered)).cycle
    assert result.outcome.value==outcome.value
    assert next(x for x in result.stage_records if x.stage is stage).status is TradingCycleStageStatus.REJECTED

def test_metrics_and_account_after_are_copied_without_portfolio_synthesis():
    cycle=builder().build(build_request()).cycle
    assert cycle.portfolio_after is None and cycle.resulting_account is not None
    assert cycle.metrics.starting_cash==10000 and cycle.metrics.ending_cash==9000
    assert cycle.metrics.equity_change==0 and cycle.metrics.market_value_after==1000

def test_risk_modified_quantity_trace():
    from tests.execution_orchestrator.helpers import real_engine,request
    from app.risk import RiskPolicy
    source=request();orchestration=real_engine(risk_policy=RiskPolicy(enabled=True,max_order_quantity="4"))[0].execute(source)
    req=build_request();cycle=builder().build(replace(req,orchestrator_result=orchestration,original_account=source.paper_account)).cycle
    assert cycle.decision_trace.requested_quantity==10 and cycle.decision_trace.approved_quantity==4 and cycle.decision_trace.planned_quantity==4

def test_failed_stage_status_and_diagnostics():
    req=build_request();req=replace(req,orchestrator_result=None,metadata={"failed_stage":"RISK","exception_type":"RiskError"})
    cycle=builder().build(req).cycle
    assert cycle.outcome is TradingCycleOutcome.FAILED and cycle.diagnostics.failed_stage is TradingCycleStage.RISK
    assert cycle.diagnostics.exception_type=="RiskError" and next(x for x in cycle.stage_records if x.stage is TradingCycleStage.RISK).status is TradingCycleStageStatus.FAILED

def test_disabled_valid_cycle_and_zero_evaluator_calls():
    evaluator=Evaluator();result=TradingCycleBuilder(TradingCyclePolicy(),evaluator).build(build_request())
    assert result.cycle.outcome is TradingCycleOutcome.DISABLED and evaluator.calls==[]

def test_evaluator_exactly_once():
    metrics=TradingCycleMetrics();evaluator=Evaluator(metrics);result=builder(evaluator).build(build_request())
    assert len(evaluator.calls)==1 and result.cycle.metrics is metrics

def test_evaluator_error_normalized_with_cause():
    evaluator=Evaluator(error=KeyError("raw"))
    with pytest.raises(TradingCycleEvaluationError) as caught:builder(evaluator).build(build_request())
    assert isinstance(caught.value.__cause__,KeyError) and len(evaluator.calls)==1

def test_deterministic_immutable_build():
    req=build_request();first=builder().build(req);second=builder().build(req)
    assert first==second and first.to_dict()==second.to_dict()
    with pytest.raises(FrozenInstanceError):first.cycle.outcome=TradingCycleOutcome.FAILED
