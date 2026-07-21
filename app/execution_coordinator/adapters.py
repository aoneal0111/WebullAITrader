from __future__ import annotations

from collections.abc import Callable

from app.execution_coordinator.contexts import (
    ComplianceEvaluationContext,
    PaperExecutionContext,
    RiskEvaluationContext,
)


def adapt_risk_evaluator(
    evaluator: Callable[..., object],
) -> Callable[[RiskEvaluationContext], object]:
    """
    Adapt the repository risk validator.

    Supports both evaluate_risk(response, snapshot) and
    evaluate_risk(response, snapshot, limits).
    """

    def adapted(context: RiskEvaluationContext) -> object:
        try:
            return evaluator(
                context.response,
                context.snapshot,
                context.limits,
            )
        except TypeError as three_argument_error:
            try:
                return evaluator(
                    context.response,
                    context.snapshot,
                )
            except TypeError:
                raise three_argument_error

    return adapted


def adapt_compliance_evaluator(
    evaluator: Callable[..., object],
) -> Callable[[ComplianceEvaluationContext], object]:
    def adapted(
        context: ComplianceEvaluationContext,
    ) -> object:
        return evaluator(
            context.proposal,
            context.account_state,
            context.market_state,
            context.risk_decision,
            context.gfv_decision,
            context.limits,
            context.kill_switch,
        )

    return adapted


def adapt_paper_executor(
    executor: Callable[..., object],
) -> Callable[[PaperExecutionContext], object]:
    def adapted(context: PaperExecutionContext) -> object:
        return executor(
            context.portfolio,
            context.proposal,
            context.compliance_decision,
            context.market_quote,
            context.execution_config,
            context.journal,
            context.equity_curve,
        )

    return adapted
