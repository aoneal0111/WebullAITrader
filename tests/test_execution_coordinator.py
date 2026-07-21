from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

import pytest

from app.execution_coordinator import (
    CoordinationRequest,
    CoordinationStage,
    CoordinationStatus,
    ExecutionCoordinator,
    adapt_compliance_evaluator,
    adapt_paper_executor,
    adapt_risk_evaluator,
)
from app.order_compliance.models import (
    OrderSide,
    OrderType,
    TradingSession,
)
from app.strategy_engine import (
    StrategyDecision,
    StrategyDecisionAction,
    StrategyOrderIntent,
)

NOW = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Approval:
    approved: bool
    reason: str


class Status(StrEnum):
    FILLED = "FILLED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class Execution:
    status: Status
    reason: str


@dataclass(frozen=True)
class Simulation:
    execution: Execution


def decision(
    action: StrategyDecisionAction = (
        StrategyDecisionAction.ENTER_LONG
    ),
) -> StrategyDecision:
    return StrategyDecision(
        symbol="AAPL",
        action=action,
        confidence=80,
        score=Decimal("0.8"),
        timestamp=NOW,
        reasons=("test",),
        source_action="BUY",
        position_quantity=Decimal("0"),
    )


def intent(
    *,
    side: OrderSide = OrderSide.BUY,
    symbol: str = "AAPL",
    timestamp: datetime = NOW,
) -> StrategyOrderIntent:
    return StrategyOrderIntent(
        timestamp=timestamp,
        request_id="req-1",
        symbol=symbol,
        side=side,
        quantity=Decimal("5"),
        order_type=OrderType.MARKET,
        requested_session=TradingSession.REGULAR,
    )


def request(
    *,
    order_intent: StrategyOrderIntent | None = None,
) -> CoordinationRequest:
    return CoordinationRequest(
        order_intent=order_intent or intent(),
        advisory_response=object(),
        snapshot=object(),
        risk_limits=object(),
        account_state=object(),
        market_state=object(),
        gfv_decision=object(),
        compliance_limits=object(),
        kill_switch=object(),
        portfolio=object(),
        market_quote=object(),
        execution_config=object(),
        journal=object(),
        equity_curve=(),
    )


def coordinator(
    *,
    risk: Approval = Approval(True, "risk approved"),
    compliance: Approval = Approval(
        True,
        "compliance approved",
    ),
    execution: Simulation = Simulation(
        Execution(Status.FILLED, "filled")
    ),
    calls: list[str] | None = None,
) -> ExecutionCoordinator:
    call_log = calls if calls is not None else []

    def proposal_factory(order_intent):
        call_log.append("proposal")
        return {"request_id": order_intent.request_id}

    def risk_evaluator(context):
        call_log.append("risk")
        return risk

    def compliance_evaluator(context):
        call_log.append("compliance")
        return compliance

    def paper_executor(context):
        call_log.append("execution")
        return execution

    return ExecutionCoordinator(
        proposal_factory=proposal_factory,
        risk_evaluator=risk_evaluator,
        compliance_evaluator=compliance_evaluator,
        paper_executor=paper_executor,
    )


def test_hold_decision_is_skipped() -> None:
    result = coordinator().coordinate(
        decision(StrategyDecisionAction.HOLD)
    )

    assert result.status is CoordinationStatus.SKIPPED
    assert result.final_stage is CoordinationStage.STRATEGY
    assert result.order_intent is None


def test_ignore_decision_is_skipped() -> None:
    result = coordinator().coordinate(
        decision(StrategyDecisionAction.IGNORE)
    )

    assert result.skipped is True


def test_executable_decision_requires_request() -> None:
    with pytest.raises(
        ValueError,
        match="requires a coordination request",
    ):
        coordinator().coordinate(decision())


def test_successful_pipeline_runs_in_order() -> None:
    calls: list[str] = []

    result = coordinator(calls=calls).coordinate(
        decision(),
        request(),
    )

    assert calls == [
        "proposal",
        "risk",
        "compliance",
        "execution",
    ]
    assert result.status is CoordinationStatus.EXECUTED
    assert result.final_stage is CoordinationStage.COMPLETE
    assert result.executed is True


def test_risk_rejection_stops_pipeline() -> None:
    calls: list[str] = []

    result = coordinator(
        risk=Approval(False, "risk rejected"),
        calls=calls,
    ).coordinate(
        decision(),
        request(),
    )

    assert calls == ["proposal", "risk"]
    assert result.status is CoordinationStatus.REJECTED
    assert result.final_stage is CoordinationStage.RISK
    assert result.compliance_decision is None
    assert result.execution_result is None


def test_compliance_rejection_stops_execution() -> None:
    calls: list[str] = []

    result = coordinator(
        compliance=Approval(
            False,
            "compliance rejected",
        ),
        calls=calls,
    ).coordinate(
        decision(),
        request(),
    )

    assert calls == [
        "proposal",
        "risk",
        "compliance",
    ]
    assert result.final_stage is (
        CoordinationStage.COMPLIANCE
    )
    assert result.execution_result is None


def test_execution_rejection_is_preserved() -> None:
    result = coordinator(
        execution=Simulation(
            Execution(Status.REJECTED, "not filled")
        )
    ).coordinate(
        decision(),
        request(),
    )

    assert result.rejected is True
    assert result.final_stage is CoordinationStage.EXECUTION
    assert result.execution_result is not None


def test_symbol_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="symbols must match"):
        coordinator().coordinate(
            decision(),
            request(order_intent=intent(symbol="MSFT")),
        )


def test_side_mismatch_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="side does not match",
    ):
        coordinator().coordinate(
            decision(),
            request(
                order_intent=intent(side=OrderSide.SELL)
            ),
        )


def test_timestamp_mismatch_is_rejected() -> None:
    other = datetime(
        2026,
        7,
        20,
        14,
        1,
        tzinfo=UTC,
    )

    with pytest.raises(
        ValueError,
        match="timestamps must match",
    ):
        coordinator().coordinate(
            decision(),
            request(
                order_intent=intent(timestamp=other)
            ),
        )


def test_exit_long_requires_sell_intent() -> None:
    result = coordinator().coordinate(
        decision(StrategyDecisionAction.EXIT_LONG),
        request(
            order_intent=intent(side=OrderSide.SELL)
        ),
    )

    assert result.executed is True


def test_trace_contains_every_completed_stage() -> None:
    result = coordinator().coordinate(
        decision(),
        request(),
    )

    assert tuple(item.stage for item in result.trace) == (
        CoordinationStage.STRATEGY,
        CoordinationStage.INTENT,
        CoordinationStage.RISK,
        CoordinationStage.COMPLIANCE,
        CoordinationStage.EXECUTION,
        CoordinationStage.COMPLETE,
    )


def test_risk_adapter_supports_three_arguments() -> None:
    received = []

    def evaluator(response, snapshot, limits):
        received.extend((response, snapshot, limits))
        return "risk"

    adapted = adapt_risk_evaluator(evaluator)
    context = type(
        "Context",
        (),
        {
            "response": 1,
            "snapshot": 2,
            "limits": 3,
        },
    )()

    assert adapted(context) == "risk"
    assert received == [1, 2, 3]


def test_risk_adapter_supports_two_arguments() -> None:
    received = []

    def evaluator(response, snapshot):
        received.extend((response, snapshot))
        return "risk"

    adapted = adapt_risk_evaluator(evaluator)
    context = type(
        "Context",
        (),
        {
            "response": 1,
            "snapshot": 2,
            "limits": 3,
        },
    )()

    assert adapted(context) == "risk"
    assert received == [1, 2]


def test_compliance_adapter_preserves_argument_order() -> None:
    received = []

    def evaluator(*args):
        received.extend(args)
        return "compliance"

    adapted = adapt_compliance_evaluator(evaluator)
    context = type(
        "Context",
        (),
        {
            "proposal": 1,
            "account_state": 2,
            "market_state": 3,
            "risk_decision": 4,
            "gfv_decision": 5,
            "limits": 6,
            "kill_switch": 7,
        },
    )()

    assert adapted(context) == "compliance"
    assert received == [1, 2, 3, 4, 5, 6, 7]


def test_paper_adapter_preserves_argument_order() -> None:
    received = []

    def executor(*args):
        received.extend(args)
        return "simulation"

    adapted = adapt_paper_executor(executor)
    context = type(
        "Context",
        (),
        {
            "portfolio": 1,
            "proposal": 2,
            "compliance_decision": 3,
            "market_quote": 4,
            "execution_config": 5,
            "journal": 6,
            "equity_curve": 7,
        },
    )()

    assert adapted(context) == "simulation"
    assert received == [1, 2, 3, 4, 5, 6, 7]
