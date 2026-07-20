from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.order_compliance.models import (
    OrderComplianceDecision, OrderSide, OrderType, ProposedOrder, TradingSession,
)
from app.paper_trading import create_portfolio, simulate_proposal
from app.paper_trading.journal import append_event
from app.paper_trading.metrics import calculate_metrics
from app.paper_trading.models import (
    EquityPoint, ExecutionStatus, JournalEventType, PaperExecutionConfig, PaperJournal,
    PaperMarketQuote,
)

NOW = datetime(2026, 7, 20, 15, tzinfo=UTC)
CONFIG = PaperExecutionConfig(5)


def _order(**changes: object) -> ProposedOrder:
    values: dict[str, object] = {
        "request_id": "req-1", "symbol": "TEST", "side": OrderSide.BUY,
        "order_type": OrderType.MARKET, "quantity": Decimal("2"),
        "limit_price": None, "stop_price": None, "requested_session": TradingSession.REGULAR,
        "created_timestamp": NOW,
    }
    values.update(changes)
    return ProposedOrder(**values)  # type: ignore[arg-type]


def _decision(order: ProposedOrder, approved: bool = True, request_id: str | None = None) -> OrderComplianceDecision:
    return OrderComplianceDecision(
        approved, "paper only", request_id or order.request_id, (), () if approved else ("test",), (),
        order.quantity, order.limit_price, order.stop_price, None, None,
    )


def _quote(**changes: object) -> PaperMarketQuote:
    values: dict[str, object] = {
        "symbol": "TEST", "bid": Decimal("99"), "ask": Decimal("100"),
        "last_price": Decimal("100"), "timestamp": NOW,
    }
    values.update(changes)
    return PaperMarketQuote(**values)  # type: ignore[arg-type]


def _state(cash: str = "1000"):
    portfolio = create_portfolio(Decimal(cash), NOW - timedelta(seconds=1))
    return portfolio, PaperJournal(), (EquityPoint(portfolio.timestamp, portfolio.equity),)


def _simulate(order: ProposedOrder, portfolio=None, journal=None, curve=None, quote=None, decision=None, config=CONFIG):
    if portfolio is None:
        portfolio, default_journal, default_curve = _state()
        journal = journal or default_journal
        curve = curve or default_curve
    return simulate_proposal(
        portfolio, order, decision or _decision(order), quote or _quote(), config,
        journal or PaperJournal(), curve,
    )


def test_market_buy_fills_at_ask_and_updates_average_cost() -> None:
    result = _simulate(_order())
    assert result.execution.status is ExecutionStatus.FILLED
    assert result.execution.fill.fill_price == Decimal("100")  # type: ignore[union-attr]
    assert result.portfolio.cash == Decimal("800")
    assert result.portfolio.positions[0].average_cost == Decimal("100")


def test_market_sell_fills_at_bid_and_realizes_pnl() -> None:
    bought = _simulate(_order(quantity=Decimal("4")))
    sell = _order(request_id="req-2", side=OrderSide.SELL, quantity=Decimal("2"), created_timestamp=NOW + timedelta(seconds=1))
    quote = _quote(bid=Decimal("110"), ask=Decimal("111"), last_price=Decimal("110"), timestamp=NOW + timedelta(seconds=1))
    result = _simulate(sell, bought.portfolio, bought.journal, bought.equity_curve, quote)
    assert result.execution.fill.realized_pnl == Decimal("20")  # type: ignore[union-attr]
    assert result.portfolio.positions[0].quantity == Decimal("2")
    assert result.metrics.win_rate == Decimal("100")


@pytest.mark.parametrize(
    "side, bid, ask, limit",
    [
        (OrderSide.BUY, "99", "100", "100"),
        (OrderSide.SELL, "101", "102", "101"),
    ],
)
def test_crossed_limit_uses_side_quote(side: OrderSide, bid: str, ask: str, limit: str) -> None:
    portfolio, journal, curve = _state()
    if side is OrderSide.SELL:
        bought = _simulate(_order(quantity=Decimal("2")), portfolio, journal, curve)
        portfolio, journal, curve = bought.portfolio, bought.journal, bought.equity_curve
    order = _order(request_id="limit", side=side, order_type=OrderType.LIMIT, quantity=Decimal("1"),
                   limit_price=Decimal(limit), created_timestamp=NOW)
    result = _simulate(order, portfolio, journal, curve, _quote(bid=Decimal(bid), ask=Decimal(ask)))
    assert result.execution.status is ExecutionStatus.FILLED
    assert result.execution.fill.fill_price == (Decimal(ask) if side is OrderSide.BUY else Decimal(bid))  # type: ignore[union-attr]


def test_not_filled_changes_only_journal() -> None:
    portfolio, journal, curve = _state()
    order = _order(order_type=OrderType.LIMIT, limit_price=Decimal("90"))
    result = _simulate(order, portfolio, journal, curve, _quote(last_price=Decimal("1")))
    assert result.execution.status is ExecutionStatus.NOT_FILLED
    assert result.portfolio == portfolio
    assert result.equity_curve == curve
    assert [event.event_type for event in result.journal.events] == [JournalEventType.PROPOSAL, JournalEventType.NOT_FILLED]


def test_rejected_or_mismatched_compliance_never_executes() -> None:
    order = _order()
    assert _simulate(order, decision=_decision(order, False)).execution.status is ExecutionStatus.REJECTED
    assert _simulate(order, decision=_decision(order, request_id="other")).execution.status is ExecutionStatus.REJECTED


@pytest.mark.parametrize("order_type", [OrderType.STOP, OrderType.STOP_LIMIT])
def test_unsupported_types_reject(order_type: OrderType) -> None:
    order = _order(order_type=order_type, stop_price=Decimal("95"), limit_price=Decimal("94"))
    assert _simulate(order).execution.status is ExecutionStatus.REJECTED


def test_insufficient_cash_and_short_sale_reject() -> None:
    assert _simulate(_order(quantity=Decimal("11"))).execution.status is ExecutionStatus.REJECTED
    assert _simulate(_order(side=OrderSide.SELL)).execution.status is ExecutionStatus.REJECTED


def test_weighted_average_cost_and_full_sale() -> None:
    first = _simulate(_order(quantity=Decimal("2")), quote=_quote(ask=Decimal("100")))
    second_order = _order(request_id="req-2", quantity=Decimal("2"), created_timestamp=NOW + timedelta(seconds=1))
    second = _simulate(second_order, first.portfolio, first.journal, first.equity_curve,
                       _quote(ask=Decimal("110"), last_price=Decimal("110"), timestamp=NOW + timedelta(seconds=1)))
    assert second.portfolio.positions[0].average_cost == Decimal("105")
    sell = _order(request_id="req-3", side=OrderSide.SELL, quantity=Decimal("4"), created_timestamp=NOW + timedelta(seconds=2))
    result = _simulate(sell, second.portfolio, second.journal, second.equity_curve,
                       _quote(bid=Decimal("115"), last_price=Decimal("115"), timestamp=NOW + timedelta(seconds=2)))
    assert not result.portfolio.positions
    assert result.portfolio.realized_pnl == Decimal("40")


def test_market_requires_side_specific_quote_without_last_fallback() -> None:
    assert _simulate(_order(), quote=_quote(ask=None)).execution.status is ExecutionStatus.REJECTED
    assert _simulate(_order(side=OrderSide.SELL), quote=_quote(bid=None)).execution.status is ExecutionStatus.REJECTED


def test_limit_cross_does_not_use_last_price() -> None:
    order = _order(order_type=OrderType.LIMIT, limit_price=Decimal("90"))
    result = _simulate(order, quote=_quote(ask=Decimal("100"), last_price=Decimal("1")))
    assert result.execution.status is ExecutionStatus.NOT_FILLED


def test_explicit_quote_age_and_timezone_are_enforced() -> None:
    assert _simulate(_order(), quote=_quote(timestamp=NOW - timedelta(seconds=6))).execution.status is ExecutionStatus.REJECTED
    assert _simulate(_order(), quote=_quote(timestamp=datetime(2026, 7, 20, 15))).execution.status is ExecutionStatus.REJECTED
    assert _simulate(_order(), config=PaperExecutionConfig(-1)).execution.status is ExecutionStatus.REJECTED


def test_quote_symbol_and_prices_fail_closed() -> None:
    assert _simulate(_order(), quote=_quote(symbol="OTHER")).execution.status is ExecutionStatus.REJECTED
    for value in (Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")):
        assert _simulate(_order(), quote=_quote(ask=value)).execution.status is ExecutionStatus.REJECTED


def test_inconsistent_equity_history_is_rejected_not_repaired() -> None:
    portfolio, journal, curve = _state()
    inconsistent = (EquityPoint(curve[-1].timestamp, Decimal("999")),)
    with pytest.raises(ValueError, match="inconsistent"):
        _simulate(_order(), portfolio, journal, inconsistent)


def test_fill_level_metrics_and_drawdown() -> None:
    journal = PaperJournal()
    for index, pnl in enumerate(("10", "-5", "20"), 1):
        journal = append_event(journal, JournalEventType.FILL, str(index), NOW, "fill", (("realized_pnl", pnl),))
    curve = (
        EquityPoint(NOW, Decimal("100")),
        EquityPoint(NOW + timedelta(seconds=1), Decimal("120")),
        EquityPoint(NOW + timedelta(seconds=2), Decimal("90")),
        EquityPoint(NOW + timedelta(seconds=3), Decimal("125")),
    )
    metrics = calculate_metrics(journal, curve)
    assert metrics.win_rate == Decimal("2") / Decimal("3") * Decimal("100")
    assert metrics.average_winner == Decimal("15")
    assert metrics.average_loser == Decimal("-5")
    assert metrics.profit_factor == Decimal("6")
    assert metrics.expectancy == Decimal("25") / Decimal("3")
    assert metrics.total_return == Decimal("25")
    assert metrics.maximum_drawdown == Decimal("25")


def test_repeated_inputs_are_deterministic_and_proposal_immutable() -> None:
    order = _order()
    first, second = _simulate(order), _simulate(order)
    assert first == second
    assert first.execution.original_proposal == order
