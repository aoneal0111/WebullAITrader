from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.execution_planner import ExecutionPlanDecision
from app.order_placement import OrderSide, OrderType
from app.paper_trading import (PaperExecutionOutcome, PaperOrderStatus, PaperPosition,
                               PaperTradingEvaluationError, PaperTradingPolicy, PaperTradingRuntime)
from tests.paper_trading.helpers import Evaluator, account, request


def runtime(evaluator=None, **policy):
    evaluator = evaluator or Evaluator()
    return PaperTradingRuntime(evaluator, PaperTradingPolicy(enabled=True, **policy)), evaluator


def position(quantity="10", cost="80", price="100", realized="0"):
    q, c, p = map(Decimal, (quantity, cost, price))
    return PaperPosition("acct", "AAPL", q, c, p, q * p, (p - c) * q, Decimal(realized))


def test_buy_new_position_and_exactly_once_evaluation():
    engine, evaluator = runtime(); result = engine.execute(request())
    assert len(evaluator.calls) == 1 and result.outcome is PaperExecutionOutcome.EXECUTED
    assert result.account.cash == Decimal("9000") and result.account.positions[0].average_cost == Decimal("100")
    assert result.order.status is PaperOrderStatus.FILLED and result.fill.quantity == 10


def test_buy_existing_position_weighted_average_cost():
    engine, _ = runtime(); result = engine.execute(request(account(positions=(position(),))))
    held = result.account.positions[0]
    assert held.quantity == 20 and held.average_cost == 90 and held.market_value == 2000
    assert held.unrealized_profit_loss == 200 and result.account.total_equity == 11000


def test_buy_insufficient_cash_rejected_without_state_change():
    state = account("999"); engine, evaluator = runtime(); result = engine.execute(request(state))
    assert len(evaluator.calls) == 1 and result.outcome is PaperExecutionOutcome.REJECTED
    assert result.account is state and result.fill is None and result.order.status is PaperOrderStatus.REJECTED


@pytest.mark.parametrize("quantity,remaining", [("4", "6"), ("10", None)])
def test_sell_partial_and_full_position(quantity, remaining):
    state = account(positions=(position(),)); engine, _ = runtime()
    result = engine.execute(request(state, side=OrderSide.SELL, quantity=quantity))
    assert result.account.cash == Decimal("10000") + Decimal(quantity) * 100
    assert (result.account.positions[0].quantity if result.account.positions else None) == (Decimal(remaining) if remaining else None)
    assert result.account.realized_profit_loss == Decimal(quantity) * 20


@pytest.mark.parametrize("cost,expected", [("80", "200"), ("120", "-200")])
def test_realized_profit_and_loss(cost, expected):
    state = account(positions=(position(cost=cost),)); engine, _ = runtime()
    result = engine.execute(request(state, side=OrderSide.SELL))
    assert result.account.realized_profit_loss == Decimal(expected)


def test_oversell_rejected():
    state = account(positions=(position(),)); engine, _ = runtime()
    result = engine.execute(request(state, side=OrderSide.SELL, quantity="11"))
    assert result.outcome is PaperExecutionOutcome.REJECTED and result.account is state


@pytest.mark.parametrize("side,limit,market,expected", [
    (OrderSide.BUY, "100", "100", True), (OrderSide.BUY, "99", "100", False),
    (OrderSide.SELL, "100", "100", True), (OrderSide.SELL, "101", "100", False)])
def test_limit_execution(side, limit, market, expected):
    state = account(positions=(position(),)) if side is OrderSide.SELL else account()
    req = request(state, side=side, order_type=OrderType.LIMIT, limit_price=limit)
    object.__setattr__(req, "market_price", Decimal(market))
    engine, evaluator = runtime(); result = engine.execute(req)
    assert (result.outcome is PaperExecutionOutcome.EXECUTED) is expected
    assert len(evaluator.calls) == int(expected)


def test_partial_fill_updates_only_filled_quantity():
    engine, evaluator = runtime(Evaluator("4"), allow_partial_fills=True); result = engine.execute(request())
    assert len(evaluator.calls) == 1 and result.outcome is PaperExecutionOutcome.PARTIALLY_EXECUTED
    assert result.account.positions[0].quantity == 4 and result.account.cash == 9600


def test_partial_fill_disabled_rejects():
    engine, _ = runtime(Evaluator("4")); result = engine.execute(request())
    assert result.outcome is PaperExecutionOutcome.REJECTED and result.fill is None


def test_commissions_and_buy_cost_basis():
    engine, _ = runtime(commission_per_order="2", commission_per_share="0.1"); result = engine.execute(request())
    assert result.fill.fees == 3 and result.account.cash == 8997
    assert result.account.positions[0].average_cost == Decimal("100.3")


@pytest.mark.parametrize("side,expected", [(OrderSide.BUY, "101"), (OrderSide.SELL, "99")])
def test_slippage_direction(side, expected):
    state = account(positions=(position(),)) if side is OrderSide.SELL else account()
    engine, _ = runtime(slippage_basis_points="100"); result = engine.execute(request(state, side=side))
    assert result.fill.price == Decimal(expected)


@pytest.mark.parametrize("decision,outcome", [(ExecutionPlanDecision.DISABLED, PaperExecutionOutcome.REJECTED),
                                               (ExecutionPlanDecision.REJECTED, PaperExecutionOutcome.REJECTED),
                                               (ExecutionPlanDecision.INVALID_RISK_RESULT, PaperExecutionOutcome.REJECTED)])
def test_ineligible_plans_are_zero_call(decision, outcome):
    engine, evaluator = runtime(); result = engine.execute(request(decision=decision))
    assert result.outcome is outcome and evaluator.calls == [] and result.order is None


def test_disabled_is_zero_call():
    evaluator = Evaluator(); engine = PaperTradingRuntime(evaluator, PaperTradingPolicy(enabled=False)); state = account()
    result = engine.execute(request(state)); assert result.outcome is PaperExecutionOutcome.DISABLED
    assert evaluator.calls == [] and result.account is state


def test_account_mismatch_is_zero_call():
    engine, evaluator = runtime(); result = engine.execute(request(account_id="other"))
    assert result.outcome is PaperExecutionOutcome.REJECTED and evaluator.calls == []


def test_evaluator_exception_is_normalized_with_cause():
    engine, _ = runtime(Evaluator(error=RuntimeError("boom")))
    with pytest.raises(PaperTradingEvaluationError) as caught: engine.execute(request())
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_models_are_immutable_and_repeated_execution_is_deterministic():
    req = request(); engine, _ = runtime(); first = engine.execute(req); second = engine.execute(req)
    assert first == second and first.to_dict() == second.to_dict()
    with pytest.raises(FrozenInstanceError): first.account.cash = Decimal("0")
