from app.execution_orchestrator.exceptions import ExecutionOrchestratorDependencyError, ExecutionOrchestratorStageError
from app.execution_orchestrator.models import (PaperTradingCycleCriteriaResult, PaperTradingCycleOutcome,
                                               PaperTradingCycleResult)
from app.execution_orchestrator.validation import validate_dependencies, validate_request
from app.execution_planner import ExecutionPlanDecision, ExecutionPlanRequest, ExecutionPlanResult
from app.paper_trading import PaperExecutionOutcome, PaperExecutionRequest, PaperExecutionResult
from app.risk import RiskContext, RiskOutcome, RiskResult
from app.strategy import StrategyContext, StrategyResult, StrategySignal


class ExecutionOrchestratorRuntime:
    def __init__(self, strategy_runtime, risk_runtime, execution_planner_runtime, paper_trading_runtime, policy):
        validate_dependencies(strategy_runtime, risk_runtime, execution_planner_runtime, paper_trading_runtime, policy)
        self._strategy = strategy_runtime; self._risk = risk_runtime; self._planner = execution_planner_runtime
        self._paper = paper_trading_runtime; self._policy = policy

    def execute(self, request):
        request = validate_request(request)
        if not self._policy.enabled:
            return self._result(request, PaperTradingCycleOutcome.DISABLED, None, None, None, None, request.paper_account,
                                (("orchestrator", False, "orchestrator disabled"),))
        configuration = request.metadata.get("strategy_configuration", {})
        if not hasattr(configuration, "items"):
            raise ExecutionOrchestratorDependencyError("strategy_configuration metadata must be a mapping")
        strategy_context = StrategyContext(request.request_id, request.portfolio, configuration, request.metadata)
        strategy_result = self._call("strategy", self._strategy.evaluate, strategy_context)
        if not isinstance(strategy_result, StrategyResult) or strategy_result.context_id != request.request_id:
            raise ExecutionOrchestratorDependencyError("strategy result identity mismatch")
        if not strategy_result.evaluated or len(strategy_result.decisions) != 1:
            return self._result(request, PaperTradingCycleOutcome.STRATEGY_REJECTED, strategy_result, None, None, None,
                                request.paper_account, (("strategy", False, "strategy did not produce exactly one evaluated decision"),))
        decision = strategy_result.decisions[0]
        if decision.signal is StrategySignal.HOLD:
            return self._result(request, PaperTradingCycleOutcome.NO_ACTION, strategy_result, None, None, None,
                                request.paper_account, (("strategy", True, "strategy produced HOLD"),))
        held = next((position for position in request.portfolio.positions if position.symbol == decision.symbol), None)
        if decision.signal is StrategySignal.EXIT and held is None:
            return self._result(request, PaperTradingCycleOutcome.NO_ACTION, strategy_result, None, None, None,
                                request.paper_account, (("strategy", True, "EXIT has no held position"),))
        risk_context = RiskContext(request.request_id, strategy_context, decision, request.requested_quantity,
                                   request.market_price, request.metadata)
        risk_result = self._call("risk", self._risk.evaluate, risk_context)
        self._validate_risk(request, risk_context, risk_result)
        if risk_result.outcome is RiskOutcome.REJECTED:
            return self._result(request, PaperTradingCycleOutcome.RISK_REJECTED, strategy_result, risk_result, None, None,
                                request.paper_account, (("strategy", True, "actionable strategy"), ("risk", False, "risk rejected")))
        plan_request = ExecutionPlanRequest(request.request_id, risk_context, risk_result, request.metadata)
        plan_result = self._call("planning", self._planner.plan, plan_request)
        if not isinstance(plan_result, ExecutionPlanResult) or plan_result.request_id != request.request_id:
            raise ExecutionOrchestratorDependencyError("execution plan result identity mismatch")
        if plan_result.decision is not ExecutionPlanDecision.PLANNED or plan_result.plan is None or len(plan_result.plan.instructions) != 1:
            return self._result(request, PaperTradingCycleOutcome.PLANNING_REJECTED, strategy_result, risk_result, plan_result,
                                None, request.paper_account, (("strategy", True, "actionable strategy"), ("risk", True, "risk approved"),
                                                              ("planning", False, "planning rejected")))
        instruction = plan_result.plan.instructions[0]
        if plan_result.plan.request_id != request.request_id or instruction.account_id != request.account_id:
            raise ExecutionOrchestratorDependencyError("planned request or account identity mismatch")
        if instruction.symbol != decision.symbol: raise ExecutionOrchestratorDependencyError("planned symbol mismatch")
        if instruction.quantity != risk_result.approved_quantity: raise ExecutionOrchestratorDependencyError("planned quantity mismatch")
        paper_request = PaperExecutionRequest(request.request_id, request.account_id, plan_result, request.paper_account,
                                              request.market_price, request.execution_timestamp, request.metadata)
        paper_result = self._call("paper_execution", self._paper.execute, paper_request)
        self._validate_paper(request, instruction, paper_result)
        outcomes = {PaperExecutionOutcome.EXECUTED: PaperTradingCycleOutcome.EXECUTED,
                    PaperExecutionOutcome.PARTIALLY_EXECUTED: PaperTradingCycleOutcome.PARTIALLY_EXECUTED,
                    PaperExecutionOutcome.NO_ACTION: PaperTradingCycleOutcome.NO_ACTION}
        outcome = outcomes.get(paper_result.outcome, PaperTradingCycleOutcome.EXECUTION_REJECTED)
        return self._result(request, outcome, strategy_result, risk_result, plan_result, paper_result,
                            paper_result.account, (("strategy", True, "actionable strategy"), ("risk", True, "risk approved"),
                                                   ("planning", True, "instruction planned"),
                                                   ("paper_execution", outcome in (PaperTradingCycleOutcome.EXECUTED, PaperTradingCycleOutcome.PARTIALLY_EXECUTED), "paper execution completed")))

    @staticmethod
    def _call(stage, operation, value):
        try: return operation(value)
        except Exception as exc: raise ExecutionOrchestratorStageError(stage, f"{stage} stage failed") from exc

    @staticmethod
    def _validate_risk(request, context, result):
        if not isinstance(result, RiskResult): raise ExecutionOrchestratorDependencyError("risk runtime returned invalid result")
        if result.context_id != request.request_id or result.context_id != context.context_id: raise ExecutionOrchestratorDependencyError("risk context identity mismatch")
        if result.strategy_decision != context.strategy_decision: raise ExecutionOrchestratorDependencyError("risk strategy decision mismatch")
        if result.requested_quantity != request.requested_quantity or result.approved_quantity > request.requested_quantity:
            raise ExecutionOrchestratorDependencyError("risk quantity mismatch")

    @staticmethod
    def _validate_paper(request, instruction, result):
        if not isinstance(result, PaperExecutionResult): raise ExecutionOrchestratorDependencyError("paper runtime returned invalid result")
        if result.request_id != request.request_id or result.account_id != request.account_id or result.account.account_id != request.account_id:
            raise ExecutionOrchestratorDependencyError("paper execution identity mismatch")
        if result.order is not None and (result.order.symbol != instruction.symbol or result.order.requested_quantity != instruction.quantity):
            raise ExecutionOrchestratorDependencyError("paper order symbol or quantity mismatch")
        if result.fill is not None and (result.fill.symbol != instruction.symbol or result.fill.quantity > instruction.quantity):
            raise ExecutionOrchestratorDependencyError("paper fill symbol or quantity mismatch")

    def _result(self, request, outcome, strategy, risk, plan, paper, account, criteria):
        return PaperTradingCycleResult(request.request_id, request.account_id, outcome, strategy, risk, plan, paper, account,
                                       tuple(PaperTradingCycleCriteriaResult(*item) for item in criteria), self._policy.version,
                                       {"deterministic": True})
