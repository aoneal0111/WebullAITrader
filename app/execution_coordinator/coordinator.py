from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.execution_coordinator.contexts import (
    ComplianceEvaluationContext,
    CoordinationRequest,
    PaperExecutionContext,
    RiskEvaluationContext,
)
from app.execution_coordinator.models import (
    CoordinationStage,
    CoordinationStatus,
    CoordinationTrace,
    ExecutionCoordinationResult,
)
from app.strategy_engine import (
    StrategyDecision,
    StrategyDecisionAction,
    StrategyOrderIntent,
)


ApprovalReader = Callable[[object], bool]
MessageReader = Callable[[object], str]


class ExecutionCoordinator:
    """
    Orchestrate an existing strategy, risk, compliance, and paper pipeline.

    All financial and regulatory state is supplied by the caller. This class
    does not size positions, manufacture approvals, modify portfolios directly,
    or access a broker.
    """

    def __init__(
        self,
        *,
        proposal_factory: Callable[[StrategyOrderIntent], object],
        risk_evaluator: Callable[[RiskEvaluationContext], object],
        compliance_evaluator: Callable[
            [ComplianceEvaluationContext], object
        ],
        paper_executor: Callable[[PaperExecutionContext], object],
        risk_approved: ApprovalReader | None = None,
        compliance_approved: ApprovalReader | None = None,
        execution_succeeded: ApprovalReader | None = None,
        decision_message: MessageReader | None = None,
    ) -> None:
        self._proposal_factory = proposal_factory
        self._risk_evaluator = risk_evaluator
        self._compliance_evaluator = compliance_evaluator
        self._paper_executor = paper_executor
        self._risk_approved = risk_approved or _approved_attribute
        self._compliance_approved = (
            compliance_approved or _approved_attribute
        )
        self._execution_succeeded = (
            execution_succeeded or _execution_success
        )
        self._decision_message = (
            decision_message or _default_message
        )

    def coordinate(
        self,
        strategy_decision: StrategyDecision,
        request: CoordinationRequest | None = None,
    ) -> ExecutionCoordinationResult:
        trace: list[CoordinationTrace] = [
            CoordinationTrace(
                CoordinationStage.STRATEGY,
                strategy_decision.creates_order_intent,
                (
                    f"Strategy produced "
                    f"{strategy_decision.action.value}."
                ),
            )
        ]

        if not strategy_decision.creates_order_intent:
            return ExecutionCoordinationResult(
                status=CoordinationStatus.SKIPPED,
                final_stage=CoordinationStage.STRATEGY,
                strategy_decision=strategy_decision,
                order_intent=None,
                proposal=None,
                risk_decision=None,
                compliance_decision=None,
                execution_result=None,
                trace=tuple(trace),
            )

        if request is None:
            raise ValueError(
                "an executable strategy decision requires "
                "a coordination request"
            )

        self._validate_intent(
            strategy_decision,
            request.order_intent,
        )

        trace.append(
            CoordinationTrace(
                CoordinationStage.INTENT,
                True,
                "Explicit caller-supplied order intent accepted.",
            )
        )

        proposal = self._proposal_factory(request.order_intent)

        risk_context = RiskEvaluationContext(
            response=request.advisory_response,
            snapshot=request.snapshot,
            limits=request.risk_limits,
        )
        risk_decision = self._risk_evaluator(risk_context)
        risk_approved = self._risk_approved(risk_decision)

        trace.append(
            CoordinationTrace(
                CoordinationStage.RISK,
                risk_approved,
                self._decision_message(risk_decision),
            )
        )

        if not risk_approved:
            return ExecutionCoordinationResult(
                status=CoordinationStatus.REJECTED,
                final_stage=CoordinationStage.RISK,
                strategy_decision=strategy_decision,
                order_intent=request.order_intent,
                proposal=proposal,
                risk_decision=risk_decision,
                compliance_decision=None,
                execution_result=None,
                trace=tuple(trace),
            )

        compliance_context = ComplianceEvaluationContext(
            proposal=proposal,
            account_state=request.account_state,
            market_state=request.market_state,
            risk_decision=risk_decision,
            gfv_decision=request.gfv_decision,
            limits=request.compliance_limits,
            kill_switch=request.kill_switch,
        )
        compliance_decision = self._compliance_evaluator(
            compliance_context
        )
        compliance_approved = self._compliance_approved(
            compliance_decision
        )

        trace.append(
            CoordinationTrace(
                CoordinationStage.COMPLIANCE,
                compliance_approved,
                self._decision_message(compliance_decision),
            )
        )

        if not compliance_approved:
            return ExecutionCoordinationResult(
                status=CoordinationStatus.REJECTED,
                final_stage=CoordinationStage.COMPLIANCE,
                strategy_decision=strategy_decision,
                order_intent=request.order_intent,
                proposal=proposal,
                risk_decision=risk_decision,
                compliance_decision=compliance_decision,
                execution_result=None,
                trace=tuple(trace),
            )

        execution_context = PaperExecutionContext(
            portfolio=request.portfolio,
            proposal=proposal,
            compliance_decision=compliance_decision,
            market_quote=request.market_quote,
            execution_config=request.execution_config,
            journal=request.journal,
            equity_curve=request.equity_curve,
        )
        execution_result = self._paper_executor(
            execution_context
        )
        succeeded = self._execution_succeeded(execution_result)

        trace.append(
            CoordinationTrace(
                CoordinationStage.EXECUTION,
                succeeded,
                self._decision_message(execution_result),
            )
        )

        if not succeeded:
            return ExecutionCoordinationResult(
                status=CoordinationStatus.REJECTED,
                final_stage=CoordinationStage.EXECUTION,
                strategy_decision=strategy_decision,
                order_intent=request.order_intent,
                proposal=proposal,
                risk_decision=risk_decision,
                compliance_decision=compliance_decision,
                execution_result=execution_result,
                trace=tuple(trace),
            )

        trace.append(
            CoordinationTrace(
                CoordinationStage.COMPLETE,
                True,
                "Paper execution pipeline completed.",
            )
        )

        return ExecutionCoordinationResult(
            status=CoordinationStatus.EXECUTED,
            final_stage=CoordinationStage.COMPLETE,
            strategy_decision=strategy_decision,
            order_intent=request.order_intent,
            proposal=proposal,
            risk_decision=risk_decision,
            compliance_decision=compliance_decision,
            execution_result=execution_result,
            trace=tuple(trace),
        )

    @staticmethod
    def _validate_intent(
        decision: StrategyDecision,
        intent: StrategyOrderIntent,
    ) -> None:
        if intent.symbol != decision.symbol:
            raise ValueError(
                "strategy decision and order intent symbols "
                "must match"
            )

        expected_side = _expected_side(decision.action)

        if intent.side.value != expected_side:
            raise ValueError(
                "order intent side does not match "
                "the strategy decision"
            )

        if intent.timestamp != decision.timestamp:
            raise ValueError(
                "strategy decision and order intent timestamps "
                "must match"
            )


def _expected_side(
    action: StrategyDecisionAction,
) -> str:
    if action in {
        StrategyDecisionAction.ENTER_LONG,
        StrategyDecisionAction.EXIT_SHORT,
    }:
        return "BUY"

    if action in {
        StrategyDecisionAction.ENTER_SHORT,
        StrategyDecisionAction.EXIT_LONG,
    }:
        return "SELL"

    raise ValueError(
        f"{action.value} does not support an order intent"
    )


def _approved_attribute(value: object) -> bool:
    approved = getattr(value, "approved", None)

    if not isinstance(approved, bool):
        raise TypeError(
            "approval decision must expose a boolean "
            "'approved' attribute"
        )

    return approved


def _execution_success(value: object) -> bool:
    execution = getattr(value, "execution", value)
    status = getattr(execution, "status", None)

    if status is None:
        raise TypeError(
            "execution result must expose a status"
        )

    raw = getattr(status, "value", status)
    return str(raw).upper() == "FILLED"


def _default_message(value: object) -> str:
    for name in (
        "reason",
        "approval_reason",
        "message",
    ):
        item = getattr(value, name, None)

        if isinstance(item, str) and item.strip():
            return item.strip()

    execution = getattr(value, "execution", None)

    if execution is not None:
        reason = getattr(execution, "reason", None)

        if isinstance(reason, str) and reason.strip():
            return reason.strip()

    return type(value).__name__
