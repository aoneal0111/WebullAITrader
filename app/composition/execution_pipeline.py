"""Composition root for the strategy-to-paper execution pipeline."""

from __future__ import annotations

from app.execution_coordinator import (
    ExecutionCoordinator,
    adapt_compliance_evaluator,
    adapt_paper_executor,
    adapt_risk_evaluator,
    create_proposed_order,
)
from app.order_compliance import evaluate_order_compliance
from app.paper_trading.simulator import simulate_proposal
from app.risk import evaluate_risk

from .paper_dependencies import create_execution_coordinator


def create_paper_execution_pipeline() -> ExecutionCoordinator:
    """Compose the production strategy-to-paper execution coordinator."""

    return create_execution_coordinator(
        proposal_factory=create_proposed_order,
        risk_evaluator=adapt_risk_evaluator(evaluate_risk),
        compliance_evaluator=adapt_compliance_evaluator(
            _evaluate_order_compliance
        ),
        paper_executor=adapt_paper_executor(simulate_proposal),
    )


def _evaluate_order_compliance(
    proposal: object,
    account_state: object,
    market_state: object,
    risk_decision: object,
    gfv_decision: object,
    limits: object,
    kill_switch: object,
) -> object:
    """
    Adapt the coordinator's positional compliance contract to the repository
    validator's keyword-only upstream-decision contract.
    """

    return evaluate_order_compliance(
        proposal,
        account_state,
        market_state,
        limits,
        kill_switch,
        gfv_decision=gfv_decision,
        risk_decision=risk_decision,
    )


__all__ = ["create_paper_execution_pipeline"]
