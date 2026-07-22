from decimal import Decimal
from app.execution_orchestrator import PaperTradingCycleOutcome
from app.execution_planner import ExecutionPlanDecision
from app.paper_trading import PaperExecutionOutcome
from app.risk import RiskOutcome
from app.strategy import StrategySignal
from app.trading_cycle.exceptions import TradingCycleEvaluationError,TradingCycleValidationError
from app.trading_cycle.models import *
from app.trading_cycle.validation import validate_dependencies,validate_request

class DefaultTradingCycleMetricsEvaluator:
    def evaluate(self,request):
        a,b=request.original_account,request.resulting_account;p,q=request.portfolio_before,request.portfolio_after
        risk=request.risk_result;plan=request.execution_plan_result;paper=request.paper_execution_result
        instruction=plan.plan.instructions[0] if plan and plan.plan else None;fill=paper.fill if paper else None
        start_cash=p.cash if p else a.cash if a else None;end_cash=q.cash if q else b.cash if b else None
        start_equity=p.equity if p else a.total_equity if a else None;end_equity=q.equity if q else b.total_equity if b else None
        return TradingCycleMetrics(start_cash,end_cash,start_equity,end_equity,end_equity-start_equity if start_equity is not None and end_equity is not None else None,
            risk.requested_quantity if risk else None,risk.approved_quantity if risk else None,instruction.quantity if instruction else None,fill.quantity if fill else None,fill.price if fill else None,fill.fees if fill else None,b.realized_profit_loss if b else None,a.unrealized_profit_loss if a else None,b.unrealized_profit_loss if b else None,p.market_value if p else a.total_market_value if a else None,q.market_value if q else b.total_market_value if b else None)

class TradingCycleBuilder:
    def __init__(self,policy,metrics_evaluator=None):validate_dependencies(policy,metrics_evaluator);self._policy=policy;self._evaluator=metrics_evaluator or DefaultTradingCycleMetricsEvaluator()
    def build(self,request):
        request=validate_request(request);request=self._effective(request)
        self._validate_structure(request)
        outcome=TradingCycleOutcome.DISABLED if not self._policy.enabled else self._outcome(request)
        symbol=self._symbol(request);identity=TradingCycleIdentity(request.cycle_id,request.request_id,request.account_id,symbol,request.mode)
        timing=TradingCycleTiming(request.started_at,request.completed_at,request.market_timestamp,request.decision_timestamp,request.execution_timestamp)
        if self._policy.enabled:
            try:metrics=self._evaluator.evaluate(request)
            except Exception as exc:raise TradingCycleEvaluationError("trading cycle metrics evaluator failed") from exc
            if not isinstance(metrics,TradingCycleMetrics):raise TradingCycleValidationError("metrics evaluator returned invalid metrics")
        else:metrics=None
        trace=self._trace(request) if self._policy.include_decision_trace else None
        diagnostics=self._diagnostics(request,outcome) if self._policy.include_diagnostics else None
        stages=self._stages(request,outcome) if self._policy.include_stage_records else ()
        cycle=TradingCycle(identity,timing,outcome,request.portfolio_before,request.portfolio_after,request.original_account,request.resulting_account,stages,trace,diagnostics,metrics if self._policy.include_metrics else None,request.metadata)
        criteria=(TradingCycleCriteriaResult("identity_continuity",True,"supplied artifact identities validated"),TradingCycleCriteriaResult("record_built",True,"immutable cycle built"))
        return TradingCycleBuildResult(cycle,criteria,{"deterministic":True,"policy_version":self._policy.version})
    def _effective(self,r):
        o=r.orchestrator_result
        if not o:return r
        from dataclasses import replace
        return replace(r,strategy_result=r.strategy_result or o.strategy_result,risk_result=r.risk_result or o.risk_result,execution_plan_result=r.execution_plan_result or o.execution_plan_result,paper_execution_result=r.paper_execution_result or o.paper_execution_result,resulting_account=r.resulting_account or o.resulting_account)
    def _validate_structure(self,r):
        if self._policy.require_portfolio_before and r.portfolio_before is None:raise TradingCycleValidationError("portfolio_before is required")
        if r.portfolio_before and r.portfolio_before.account_id!=r.account_id:raise TradingCycleValidationError("portfolio account mismatch")
        for a in (r.original_account,r.resulting_account):
            if a and a.account_id!=r.account_id:raise TradingCycleValidationError("paper account mismatch")
        o=r.orchestrator_result
        if o and (o.request_id!=r.request_id or o.account_id!=r.account_id):raise TradingCycleValidationError("orchestrator identity mismatch")
        s,rr,plan,paper=r.strategy_result,r.risk_result,r.execution_plan_result,r.paper_execution_result
        if s and s.context_id!=r.request_id:raise TradingCycleValidationError("strategy request identity mismatch")
        decision=s.decisions[0] if s and len(s.decisions)==1 else None
        if rr and (rr.context_id!=r.request_id or decision and rr.strategy_decision!=decision):raise TradingCycleValidationError("risk identity mismatch")
        if plan:
            if plan.request_id!=r.request_id:raise TradingCycleValidationError("planning request identity mismatch")
            if plan.plan:
                ins=plan.plan.instructions[0]
                if plan.plan.request_id!=r.request_id or ins.account_id!=r.account_id:raise TradingCycleValidationError("instruction identity mismatch")
                if decision and ins.symbol!=decision.symbol:raise TradingCycleValidationError("instruction symbol mismatch")
                if rr and ins.quantity!=rr.approved_quantity:raise TradingCycleValidationError("instruction quantity mismatch")
        if paper:
            if paper.request_id!=r.request_id or paper.account_id!=r.account_id or paper.account.account_id!=r.account_id:raise TradingCycleValidationError("paper execution identity mismatch")
            ins=plan.plan.instructions[0] if plan and plan.plan else None
            for artifact in (paper.order,paper.fill):
                if artifact and ins and (artifact.symbol!=ins.symbol or (artifact.quantity if artifact is paper.fill else artifact.requested_quantity)>ins.quantity):raise TradingCycleValidationError("paper symbol or quantity mismatch")
            if r.resulting_account and r.resulting_account!=paper.account:raise TradingCycleValidationError("resulting account mismatch")
    @staticmethod
    def _outcome(r):
        if r.orchestrator_result:return TradingCycleOutcome(r.orchestrator_result.outcome.value)
        if r.metadata.get("failed_stage"):return TradingCycleOutcome.FAILED
        if r.paper_execution_result:
            return {PaperExecutionOutcome.EXECUTED:TradingCycleOutcome.EXECUTED,PaperExecutionOutcome.PARTIALLY_EXECUTED:TradingCycleOutcome.PARTIALLY_EXECUTED,PaperExecutionOutcome.NO_ACTION:TradingCycleOutcome.NO_ACTION}.get(r.paper_execution_result.outcome,TradingCycleOutcome.EXECUTION_REJECTED)
        if r.execution_plan_result and r.execution_plan_result.decision is not ExecutionPlanDecision.PLANNED:return TradingCycleOutcome.PLANNING_REJECTED
        if r.risk_result and r.risk_result.outcome is RiskOutcome.REJECTED:return TradingCycleOutcome.RISK_REJECTED
        if r.strategy_result:
            if not r.strategy_result.evaluated or len(r.strategy_result.decisions)!=1:return TradingCycleOutcome.STRATEGY_REJECTED
            if r.strategy_result.decisions[0].signal in (StrategySignal.HOLD,StrategySignal.EXIT):return TradingCycleOutcome.NO_ACTION
        return TradingCycleOutcome.FAILED
    @staticmethod
    def _symbol(r):
        if r.strategy_result and len(r.strategy_result.decisions)==1:return r.strategy_result.decisions[0].symbol
        if r.execution_plan_result and r.execution_plan_result.plan:return r.execution_plan_result.plan.instructions[0].symbol
        if r.paper_execution_result and r.paper_execution_result.order:return r.paper_execution_result.order.symbol
        return None
    @staticmethod
    def _trace(r):
        s=r.strategy_result;d=s.decisions[0] if s and len(s.decisions)==1 else None;rr=r.risk_result;plan=r.execution_plan_result;ins=plan.plan.instructions[0] if plan and plan.plan else None;paper=r.paper_execution_result;fill=paper.fill if paper else None
        return TradingDecisionTrace(d.signal.value if d else None,d.confidence if d else None,d.reasons if d else (),rr.requested_quantity if rr else None,rr.outcome.value if rr else None,rr.approved_quantity if rr else None,tuple(x.name for x in rr.criteria_results if not x.passed) if rr else (),plan.decision.value if plan else None,ins.side.value if ins else None,ins.quantity if ins else None,ins.order_type.value if ins else None,ins.time_in_force.value if ins else None,ins.limit_price if ins else None,ins.stop_price if ins else None,paper.outcome.value if paper else None,fill.quantity if fill else None,fill.price if fill else None,fill.fees if fill else None,r.resulting_account.realized_profit_loss if r.resulting_account else None,{})
    @staticmethod
    def _diagnostics(r,outcome):
        reject={TradingCycleOutcome.STRATEGY_REJECTED:TradingCycleStage.STRATEGY,TradingCycleOutcome.RISK_REJECTED:TradingCycleStage.RISK,TradingCycleOutcome.PLANNING_REJECTED:TradingCycleStage.PLANNING,TradingCycleOutcome.EXECUTION_REJECTED:TradingCycleStage.EXECUTION}.get(outcome)
        failed=TradingCycleStage(r.metadata["failed_stage"]) if outcome is TradingCycleOutcome.FAILED and r.metadata.get("failed_stage") else None
        codes=tuple(x.name for x in r.risk_result.criteria_results if not x.passed) if r.risk_result else ()
        return TradingCycleDiagnostics((),(),failed,reject,r.metadata.get("exception_type"),codes,{})
    @staticmethod
    def _stages(r,outcome):
        statuses={s:TradingCycleStageStatus.SKIPPED for s in TradingCycleStage};statuses[TradingCycleStage.INPUT]=TradingCycleStageStatus.COMPLETED;statuses[TradingCycleStage.PORTFOLIO]=TradingCycleStageStatus.COMPLETED
        order=(TradingCycleStage.STRATEGY,TradingCycleStage.RISK,TradingCycleStage.PLANNING,TradingCycleStage.EXECUTION)
        supplied=(r.strategy_result,r.risk_result,r.execution_plan_result,r.paper_execution_result)
        for stage,value in zip(order,supplied):
            if value is not None:statuses[stage]=TradingCycleStageStatus.COMPLETED
        rejected={TradingCycleOutcome.STRATEGY_REJECTED:TradingCycleStage.STRATEGY,TradingCycleOutcome.RISK_REJECTED:TradingCycleStage.RISK,TradingCycleOutcome.PLANNING_REJECTED:TradingCycleStage.PLANNING,TradingCycleOutcome.EXECUTION_REJECTED:TradingCycleStage.EXECUTION}.get(outcome)
        if rejected:statuses[rejected]=TradingCycleStageStatus.REJECTED
        if outcome is TradingCycleOutcome.FAILED and r.metadata.get("failed_stage"):statuses[TradingCycleStage(r.metadata["failed_stage"])]=TradingCycleStageStatus.FAILED
        statuses[TradingCycleStage.COMPLETED]=TradingCycleStageStatus.COMPLETED
        return tuple(TradingCycleStageRecord(s,statuses[s],outcome.value,()) for s in TradingCycleStage)
