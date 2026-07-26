from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.execution_coordinator import (
    CoordinationRequest,
    CoordinationStage,
    CoordinationStatus,
    CoordinationTrace,
    ExecutionCoordinationResult,
)
from app.order_compliance.models import (
    OrderSide,
    OrderType,
    TradingSession,
)
from app.paper_session import (
    PaperSessionStatus,
    close_paper_session,
    create_paper_session,
    paper_session_to_dict,
    paper_session_to_json,
    process_decision,
)
from app.paper_trading.metrics import calculate_metrics
from app.paper_trading.models import (
    EquityPoint,
    ExecutionStatus,
    PaperExecutionResult,
    PaperFill,
    SimulationResult,
)
from app.strategy_engine import (
    StrategyDecision,
    StrategyDecisionAction,
    StrategyOrderIntent,
)


NOW = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=1)


def strategy_decision(
    action: StrategyDecisionAction = (
        StrategyDecisionAction.ENTER_LONG
    ),
    timestamp: datetime = LATER,
) -> StrategyDecision:
    return StrategyDecision(
        symbol="AAPL",
        action=action,
        confidence=80,
        score=Decimal("0.8"),
        timestamp=timestamp,
        reasons=("test",),
        source_action="BUY",
        position_quantity=Decimal("0"),
    )


def order_intent(
    request_id: str = "req-1",
) -> StrategyOrderIntent:
    return StrategyOrderIntent(
        timestamp=LATER,
        request_id=request_id,
        symbol="AAPL",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        order_type=OrderType.MARKET,
        requested_session=TradingSession.REGULAR,
    )


def request(
    request_id: str = "req-1",
) -> CoordinationRequest:
    return CoordinationRequest(
        order_intent=order_intent(request_id),
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


class StubCoordinator:
    def __init__(
        self,
        result_factory,
    ) -> None:
        self.result_factory = result_factory
        self.received_request = None

    def coordinate(
        self,
        decision,
        coordination_request=None,
    ):
        self.received_request = coordination_request
        return self.result_factory(
            decision,
            coordination_request,
        )


def skipped_result(decision, request):
    return ExecutionCoordinationResult(
        status=CoordinationStatus.SKIPPED,
        final_stage=CoordinationStage.STRATEGY,
        strategy_decision=decision,
        order_intent=None,
        proposal=None,
        risk_decision=None,
        compliance_decision=None,
        execution_result=None,
        trace=(
            CoordinationTrace(
                CoordinationStage.STRATEGY,
                False,
                "Strategy skipped.",
            ),
        ),
    )


def rejected_result(decision, request):
    return ExecutionCoordinationResult(
        status=CoordinationStatus.REJECTED,
        final_stage=CoordinationStage.RISK,
        strategy_decision=decision,
        order_intent=request.order_intent,
        proposal=object(),
        risk_decision=object(),
        compliance_decision=None,
        execution_result=None,
        trace=(
            CoordinationTrace(
                CoordinationStage.RISK,
                False,
                "Risk rejected.",
            ),
        ),
    )


def filled_result(decision, request):
    before = request.portfolio
    quote_time = decision.timestamp
    fill_price = Decimal("100")
    quantity = Decimal("1")
    after = replace(
        before,
        cash=before.cash - fill_price,
        positions=before.positions,
        unrealized_pnl=Decimal("0"),
        equity=before.equity,
        timestamp=quote_time,
    )
    fill = PaperFill(
        request_id=request.order_intent.request_id,
        symbol="AAPL",
        side="BUY",
        quantity=quantity,
        fill_price=fill_price,
        notional=fill_price,
        realized_pnl=Decimal("0"),
        timestamp=quote_time,
    )
    execution = PaperExecutionResult(
        status=ExecutionStatus.FILLED,
        reason="Filled.",
        original_proposal=object(),
        fill=fill,
        portfolio_before=before,
        portfolio_after=after,
    )
    curve = (
        *request.equity_curve,
        EquityPoint(
            timestamp=quote_time,
            equity=after.equity,
        ),
    )
    simulation = SimulationResult(
        execution=execution,
        portfolio=after,
        journal=request.journal,
        equity_curve=curve,
        metrics=calculate_metrics(
            request.journal,
            curve,
        ),
    )
    return ExecutionCoordinationResult(
        status=CoordinationStatus.EXECUTED,
        final_stage=CoordinationStage.COMPLETE,
        strategy_decision=decision,
        order_intent=request.order_intent,
        proposal=object(),
        risk_decision=object(),
        compliance_decision=object(),
        execution_result=simulation,
        trace=(
            CoordinationTrace(
                CoordinationStage.COMPLETE,
                True,
                "Paper execution completed.",
            ),
        ),
    )


def test_create_session() -> None:
    session = create_paper_session(
        session_id="session-1",
        initial_cash=Decimal("10000"),
        started_at=NOW,
    )

    assert session.status is PaperSessionStatus.ACTIVE
    assert session.portfolio.cash == Decimal("10000")
    assert session.equity_curve[-1].equity == Decimal(
        "10000"
    )
    assert session.statistics.decisions_processed == 0


def test_session_requires_timezone_aware_start() -> None:
    with pytest.raises(
        ValueError,
        match="timezone-aware",
    ):
        create_paper_session(
            session_id="session-1",
            initial_cash=Decimal("10000"),
            started_at=datetime(2026, 7, 20),
        )


def test_hold_advances_statistics() -> None:
    session = create_paper_session(
        session_id="session-1",
        initial_cash=Decimal("10000"),
        started_at=NOW,
    )

    result = process_decision(
        session,
        coordinator=StubCoordinator(skipped_result),
        strategy_decision=strategy_decision(
            StrategyDecisionAction.HOLD
        ),
    )

    updated = result.session

    assert updated.statistics.decisions_processed == 1
    assert updated.statistics.decisions_skipped == 1
    assert updated.statistics.orders_attempted == 0


def test_risk_rejection_is_recorded() -> None:
    session = create_paper_session(
        session_id="session-1",
        initial_cash=Decimal("10000"),
        started_at=NOW,
    )

    result = process_decision(
        session,
        coordinator=StubCoordinator(rejected_result),
        strategy_decision=strategy_decision(),
        request=request(),
    )

    updated = result.session

    assert updated.statistics.orders_attempted == 1
    assert updated.statistics.orders_rejected == 1
    assert updated.portfolio == session.portfolio


def test_filled_result_advances_state() -> None:
    session = create_paper_session(
        session_id="session-1",
        initial_cash=Decimal("10000"),
        started_at=NOW,
    )

    result = process_decision(
        session,
        coordinator=StubCoordinator(filled_result),
        strategy_decision=strategy_decision(),
        request=request(),
    )

    updated = result.session

    assert updated.statistics.orders_filled == 1
    assert updated.portfolio.cash == Decimal("9900")
    assert len(updated.equity_curve) == 2


def test_request_uses_authoritative_session_state() -> None:
    session = create_paper_session(
        session_id="session-1",
        initial_cash=Decimal("10000"),
        started_at=NOW,
    )
    coordinator = StubCoordinator(rejected_result)

    process_decision(
        session,
        coordinator=coordinator,
        strategy_decision=strategy_decision(),
        request=request(),
    )

    assert coordinator.received_request.portfolio is (
        session.portfolio
    )
    assert coordinator.received_request.journal is (
        session.journal
    )
    assert coordinator.received_request.equity_curve is (
        session.equity_curve
    )


def test_original_session_is_immutable() -> None:
    session = create_paper_session(
        session_id="session-1",
        initial_cash=Decimal("10000"),
        started_at=NOW,
    )

    result = process_decision(
        session,
        coordinator=StubCoordinator(rejected_result),
        strategy_decision=strategy_decision(),
        request=request(),
    )

    updated = result.session

    assert updated is not session
    assert session.statistics.decisions_processed == 0
    assert session.processed_request_ids == ()


def test_request_id_is_recorded() -> None:
    session = create_paper_session(
        session_id="session-1",
        initial_cash=Decimal("10000"),
        started_at=NOW,
    )

    result = process_decision(
        session,
        coordinator=StubCoordinator(rejected_result),
        strategy_decision=strategy_decision(),
        request=request("req-25"),
    )

    updated = result.session

    assert updated.processed_request_ids == ("req-25",)


def test_duplicate_request_id_is_rejected() -> None:
    session = create_paper_session(
        session_id="session-1",
        initial_cash=Decimal("10000"),
        started_at=NOW,
    )
    coordinator = StubCoordinator(rejected_result)

    result = process_decision(
        session,
        coordinator=coordinator,
        strategy_decision=strategy_decision(),
        request=request("req-1"),
    )

    updated = result.session

    with pytest.raises(
        ValueError,
        match="duplicate request ID",
    ):
        process_decision(updated,
            coordinator=coordinator,
            strategy_decision=strategy_decision(),
            request=request("req-1"),
        )


def test_skipped_decision_has_no_request_id() -> None:
    session = create_paper_session(
        session_id="session-1",
        initial_cash=Decimal("10000"),
        started_at=NOW,
    )

    result = process_decision(
        session,
        coordinator=StubCoordinator(skipped_result),
        strategy_decision=strategy_decision(
            StrategyDecisionAction.IGNORE
        ),
    )

    updated = result.session

    assert updated.processed_request_ids == ()
    assert updated.events[0].request_id is None


def test_event_sequence_increments() -> None:
    session = create_paper_session(
        session_id="session-1",
        initial_cash=Decimal("10000"),
        started_at=NOW,
    )
    coordinator = StubCoordinator(skipped_result)

    first_result = process_decision(
        session,
        coordinator=coordinator,
        strategy_decision=strategy_decision(
            StrategyDecisionAction.HOLD
        ),
    )
    second_result = process_decision(first_result.session,
        coordinator=coordinator,
        strategy_decision=strategy_decision(
            StrategyDecisionAction.HOLD,
            LATER + timedelta(minutes=1),
        ),
    )

    assert tuple(
        event.sequence
        for event in second_result.session.events
    ) == (1, 2)


def test_decision_before_session_is_rejected() -> None:
    session = create_paper_session(
        session_id="session-1",
        initial_cash=Decimal("10000"),
        started_at=NOW,
    )

    with pytest.raises(
        ValueError,
        match="predates",
    ):
        process_decision(
            session,
            coordinator=StubCoordinator(skipped_result),
            strategy_decision=strategy_decision(
                StrategyDecisionAction.HOLD,
                NOW - timedelta(seconds=1),
            ),
        )


def test_close_session() -> None:
    session = create_paper_session(
        session_id="session-1",
        initial_cash=Decimal("10000"),
        started_at=NOW,
    )

    closed = close_paper_session(
        session,
        ended_at=LATER,
    )

    assert closed.status is PaperSessionStatus.CLOSED
    assert closed.ended_at == LATER


def test_closed_session_cannot_process() -> None:
    session = create_paper_session(
        session_id="session-1",
        initial_cash=Decimal("10000"),
        started_at=NOW,
    )
    closed = close_paper_session(
        session,
        ended_at=LATER,
    )

    with pytest.raises(
        RuntimeError,
        match="closed",
    ):
        process_decision(
            closed,
            coordinator=StubCoordinator(skipped_result),
            strategy_decision=strategy_decision(
                StrategyDecisionAction.HOLD
            ),
        )


def test_session_cannot_close_twice() -> None:
    session = create_paper_session(
        session_id="session-1",
        initial_cash=Decimal("10000"),
        started_at=NOW,
    )
    closed = close_paper_session(
        session,
        ended_at=LATER,
    )

    with pytest.raises(
        RuntimeError,
        match="already closed",
    ):
        close_paper_session(
            closed,
            ended_at=LATER,
        )


def test_close_cannot_precede_activity() -> None:
    session = create_paper_session(
        session_id="session-1",
        initial_cash=Decimal("10000"),
        started_at=NOW,
    )
    result = process_decision(
        session,
        coordinator=StubCoordinator(skipped_result),
        strategy_decision=strategy_decision(
            StrategyDecisionAction.HOLD
        ),
    )

    with pytest.raises(
        ValueError,
        match="precede",
    ):
        close_paper_session(result.session,
            ended_at=NOW,
        )


def test_dict_serialization_is_json_safe() -> None:
    session = create_paper_session(
        session_id="session-1",
        initial_cash=Decimal("10000"),
        started_at=NOW,
    )

    value = paper_session_to_dict(session)

    assert value["status"] == "ACTIVE"
    assert value["portfolio"]["cash"] == "10000"
    assert value["started_at"] == NOW.isoformat()


def test_json_serialization_is_deterministic() -> None:
    session = create_paper_session(
        session_id="session-1",
        initial_cash=Decimal("10000"),
        started_at=NOW,
    )

    first = paper_session_to_json(session)
    second = paper_session_to_json(session)

    assert first == second
    assert json.loads(first)["schema_version"] == "1"


def test_repeated_replay_is_deterministic() -> None:
    first = create_paper_session(
        session_id="session-1",
        initial_cash=Decimal("10000"),
        started_at=NOW,
    )
    second = create_paper_session(
        session_id="session-1",
        initial_cash=Decimal("10000"),
        started_at=NOW,
    )

    first_result = process_decision(
        first,
        coordinator=StubCoordinator(rejected_result),
        strategy_decision=strategy_decision(),
        request=request(),
    )
    second_result = process_decision(
        second,
        coordinator=StubCoordinator(rejected_result),
        strategy_decision=strategy_decision(),
        request=request(),
    )

    assert (
        paper_session_to_json(first_result.session)
        == paper_session_to_json(second_result.session)
    )



