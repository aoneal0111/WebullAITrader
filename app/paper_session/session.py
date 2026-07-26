from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from app.execution_coordinator import (
    CoordinationRequest,
    ExecutionCoordinator,
)
from app.paper_session.models import (
    PaperSessionEvent,
    PaperSessionStatus,
    PaperTradingSession,
    ProcessDecisionResult,
)
from app.paper_session.statistics import (
    advance_statistics,
    initial_statistics,
)
from app.paper_trading.metrics import calculate_metrics
from app.paper_trading.models import (
    EquityPoint,
    PaperJournal,
    SimulationResult,
)
from app.paper_trading.portfolio import create_portfolio
from app.strategy_engine import StrategyDecision


SCHEMA_VERSION = "1"


def create_paper_session(
    *,
    session_id: str,
    initial_cash: Decimal,
    started_at: datetime,
) -> PaperTradingSession:
    if started_at.tzinfo is None:
        raise ValueError(
            "session start must be timezone-aware"
        )

    portfolio = create_portfolio(
        initial_cash,
        started_at,
    )
    journal = PaperJournal()
    equity_curve = (
        EquityPoint(
            timestamp=started_at,
            equity=portfolio.equity,
        ),
    )
    metrics = calculate_metrics(
        journal,
        equity_curve,
    )

    return PaperTradingSession(
        schema_version=SCHEMA_VERSION,
        session_id=session_id,
        status=PaperSessionStatus.ACTIVE,
        started_at=started_at,
        ended_at=None,
        portfolio=portfolio,
        journal=journal,
        equity_curve=equity_curve,
        metrics=metrics,
        statistics=initial_statistics(portfolio),
        processed_request_ids=(),
        events=(),
        last_coordination_result=None,
    )


def process_decision(
    session: PaperTradingSession,
    *,
    coordinator: ExecutionCoordinator,
    strategy_decision: StrategyDecision,
    request: CoordinationRequest | None = None,
) -> ProcessDecisionResult:
    if session.status is not PaperSessionStatus.ACTIVE:
        raise RuntimeError(
            "cannot process a closed paper session"
        )

    if strategy_decision.timestamp < session.started_at:
        raise ValueError(
            "strategy decision predates the session"
        )

    request_id = _request_id(request)

    if (
        request_id is not None
        and request_id in session.processed_request_ids
    ):
        raise ValueError(
            f"duplicate request ID: {request_id}"
        )

    authoritative_request = _authoritative_request(
        session,
        request,
    )

    coordination = coordinator.coordinate(
        strategy_decision,
        authoritative_request,
    )

    portfolio = session.portfolio
    journal = session.journal
    equity_curve = session.equity_curve
    metrics = session.metrics

    if coordination.execution_result is not None:
        simulation = coordination.execution_result

        if not isinstance(simulation, SimulationResult):
            raise TypeError(
                "paper session requires a SimulationResult"
            )

        portfolio = simulation.portfolio
        journal = simulation.journal
        equity_curve = simulation.equity_curve
        metrics = simulation.metrics

    statistics = advance_statistics(
        session.statistics,
        coordination,
        portfolio,
    )

    processed_ids = session.processed_request_ids

    if request_id is not None:
        processed_ids = (*processed_ids, request_id)

    event = PaperSessionEvent(
        sequence=len(session.events) + 1,
        timestamp=strategy_decision.timestamp,
        request_id=request_id,
        status=coordination.status.value,
        final_stage=coordination.final_stage.value,
        message=_coordination_message(coordination),
    )

    updated_session = replace(
        session,
        portfolio=portfolio,
        journal=journal,
        equity_curve=equity_curve,
        metrics=metrics,
        statistics=statistics,
        processed_request_ids=processed_ids,
        events=(*session.events, event),
        last_coordination_result=coordination,
    )

    return ProcessDecisionResult(
        session=updated_session,
        coordination=coordination,
    )


def close_paper_session(
    session: PaperTradingSession,
    *,
    ended_at: datetime,
) -> PaperTradingSession:
    if session.status is PaperSessionStatus.CLOSED:
        raise RuntimeError(
            "paper session is already closed"
        )

    if ended_at.tzinfo is None:
        raise ValueError(
            "session end must be timezone-aware"
        )

    latest_timestamp = max(
        session.started_at,
        session.portfolio.timestamp,
        session.equity_curve[-1].timestamp,
        *(
            event.timestamp
            for event in session.events
        ),
    )

    if ended_at < latest_timestamp:
        raise ValueError(
            "session end cannot precede session activity"
        )

    return replace(
        session,
        status=PaperSessionStatus.CLOSED,
        ended_at=ended_at,
    )


def _authoritative_request(
    session: PaperTradingSession,
    request: CoordinationRequest | None,
) -> CoordinationRequest | None:
    if request is None:
        return None

    return replace(
        request,
        portfolio=session.portfolio,
        journal=session.journal,
        equity_curve=session.equity_curve,
    )


def _request_id(
    request: CoordinationRequest | None,
) -> str | None:
    if request is None:
        return None

    value = request.order_intent.request_id.strip()

    if not value:
        raise ValueError("request ID is required")

    return value


def _coordination_message(coordination) -> str:
    if coordination.trace:
        return coordination.trace[-1].message

    return (
        f"Coordination ended at "
        f"{coordination.final_stage.value}."
    )

