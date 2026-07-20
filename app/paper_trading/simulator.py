from __future__ import annotations

from decimal import Decimal

from app.order_compliance.models import OrderComplianceDecision, ProposedOrder
from app.paper_trading.execution import evaluate_fill
from app.paper_trading.journal import append_event
from app.paper_trading.metrics import calculate_metrics
from app.paper_trading.models import (
    EquityPoint, ExecutionStatus, JournalEventType, PaperExecutionConfig, PaperExecutionResult,
    PaperFill, PaperJournal, PaperMarketQuote, PaperPortfolio, SimulationResult,
)
from app.paper_trading.portfolio import apply_fill


def simulate_proposal(
    portfolio: PaperPortfolio,
    proposal: ProposedOrder,
    compliance_decision: OrderComplianceDecision,
    market_quote: PaperMarketQuote,
    execution_config: PaperExecutionConfig,
    journal: PaperJournal,
    equity_curve: tuple[EquityPoint, ...],
) -> SimulationResult:
    """Apply one paper-only proposal as a deterministic immutable transition."""
    if not _valid_inputs(portfolio, proposal, compliance_decision, market_quote, execution_config, journal, equity_curve):
        raise ValueError("paper simulation inputs are missing, malformed, or inconsistent")
    proposal_journal = append_event(
        journal, JournalEventType.PROPOSAL, proposal.request_id, proposal.created_timestamp,
        "Paper proposal received.", (("symbol", proposal.symbol), ("quantity", str(proposal.quantity))),
    )
    if not compliance_decision.approved or compliance_decision.request_id != proposal.request_id:
        reason = "Order compliance approval is missing, rejected, or mismatched."
        return _unchanged_result(portfolio, proposal, proposal_journal, equity_curve, reason, ExecutionStatus.REJECTED)
    fill_evaluation = evaluate_fill(proposal, market_quote, execution_config)
    if fill_evaluation.status is not ExecutionStatus.FILLED:
        return _unchanged_result(
            portfolio, proposal, proposal_journal, equity_curve, fill_evaluation.reason, fill_evaluation.status
        )
    if not _positive(market_quote.last_price):
        return _unchanged_result(
            portfolio, proposal, proposal_journal, equity_curve,
            "A valid last price is required to mark the filled portfolio.", ExecutionStatus.REJECTED,
        )
    assert fill_evaluation.fill_price is not None
    try:
        updated, realized = apply_fill(
            portfolio, proposal.symbol, proposal.side, proposal.quantity,
            fill_evaluation.fill_price, market_quote.last_price, market_quote.timestamp,
        )
    except ValueError as exc:
        return _unchanged_result(portfolio, proposal, proposal_journal, equity_curve, str(exc), ExecutionStatus.REJECTED)
    fill = PaperFill(
        proposal.request_id, proposal.symbol.strip().upper(), proposal.side.value, proposal.quantity,
        fill_evaluation.fill_price, proposal.quantity * fill_evaluation.fill_price,
        realized, market_quote.timestamp,
    )
    filled_journal = append_event(
        proposal_journal, JournalEventType.FILL, proposal.request_id, market_quote.timestamp,
        "Paper fill recorded.", (("fill_price", str(fill.fill_price)), ("quantity", str(fill.quantity)),
                                 ("realized_pnl", str(fill.realized_pnl)),
                                 ("side", fill.side), ("symbol", fill.symbol)),
    )
    updated_journal = append_event(
        filled_journal, JournalEventType.PORTFOLIO_CHANGE, proposal.request_id, market_quote.timestamp,
        "Paper portfolio updated.", (("cash", str(updated.cash)), ("equity", str(updated.equity))),
    )
    updated_curve = (*equity_curve, EquityPoint(market_quote.timestamp, updated.equity))
    execution = PaperExecutionResult(ExecutionStatus.FILLED, fill_evaluation.reason, proposal, fill, portfolio, updated)
    return SimulationResult(execution, updated, updated_journal, updated_curve, calculate_metrics(updated_journal, updated_curve))


def _unchanged_result(
    portfolio: PaperPortfolio, proposal: ProposedOrder, journal: PaperJournal,
    curve: tuple[EquityPoint, ...], reason: str, status: ExecutionStatus,
) -> SimulationResult:
    event_type = JournalEventType.NOT_FILLED if status is ExecutionStatus.NOT_FILLED else JournalEventType.REJECTION
    updated_journal = append_event(journal, event_type, proposal.request_id, proposal.created_timestamp, reason)
    execution = PaperExecutionResult(status, reason, proposal, None, portfolio, portfolio)
    return SimulationResult(execution, portfolio, updated_journal, curve, calculate_metrics(updated_journal, curve))


def _valid_inputs(
    portfolio: object, proposal: object, decision: object, quote: object, config: object,
    journal: object, curve: object,
) -> bool:
    if not all((isinstance(portfolio, PaperPortfolio), isinstance(proposal, ProposedOrder),
                isinstance(decision, OrderComplianceDecision), isinstance(quote, PaperMarketQuote),
                isinstance(config, PaperExecutionConfig), isinstance(journal, PaperJournal),
                isinstance(curve, tuple) and bool(curve))):
        return False
    if portfolio.timestamp.tzinfo is None or proposal.created_timestamp.tzinfo is None:
        return False
    if not _nonnegative(portfolio.cash) or not _nonnegative(portfolio.equity):
        return False
    if not all(isinstance(point, EquityPoint) and point.timestamp.tzinfo is not None and _nonnegative(point.equity) for point in curve):
        return False
    return curve[-1].equity == portfolio.equity and curve[-1].timestamp <= proposal.created_timestamp


def _positive(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _nonnegative(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= 0
