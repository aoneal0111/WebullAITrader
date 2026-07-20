from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.compliance.models import AccountType, SellComplianceDecision
from app.order_compliance.kill_switch import KillSwitchState
from app.order_compliance.limits import DEFAULT_LIMITS
from app.order_compliance.models import (
    AccountComplianceState, MarketComplianceState, MarketStatus, OrderSide, OrderType,
    ProposedOrder, SymbolStatus, TradingSession,
)
from app.order_compliance.validator import evaluate_order_compliance, order_fingerprint
from app.risk.models import RiskDecision

NOW = datetime(2026, 7, 20, 15, 0, tzinfo=UTC)


def _order(**changes: object) -> ProposedOrder:
    values: dict[str, object] = {
        "request_id": "req-1", "symbol": "TEST", "side": OrderSide.BUY,
        "order_type": OrderType.LIMIT, "quantity": Decimal("5"),
        "limit_price": Decimal("100.00"), "stop_price": None,
        "requested_session": TradingSession.REGULAR, "created_timestamp": NOW,
    }
    values.update(changes)
    return ProposedOrder(**values)  # type: ignore[arg-type]


def _account(**changes: object) -> AccountComplianceState:
    values: dict[str, object] = {
        "account_type": AccountType.MARGIN, "account_equity": Decimal("10000"),
        "current_daily_realized_pnl": Decimal("0"), "current_daily_unrealized_pnl": Decimal("0"),
        "trades_executed_today": 0, "open_order_request_ids": (), "open_order_fingerprints": (),
        "current_symbol_position_quantity": Decimal("20"), "current_symbol_market_value": Decimal("500"),
        "current_total_gross_exposure": Decimal("1000"), "current_timestamp": NOW,
    }
    values.update(changes)
    return AccountComplianceState(**values)  # type: ignore[arg-type]


def _market(**changes: object) -> MarketComplianceState:
    values: dict[str, object] = {
        "symbol": "TEST", "market_status": MarketStatus.OPEN, "symbol_status": SymbolStatus.TRADABLE,
        "regular_session_open": NOW - timedelta(hours=1), "regular_session_close": NOW + timedelta(hours=5),
        "extended_session_open": NOW - timedelta(hours=3), "extended_session_close": NOW + timedelta(hours=8),
        "price_tick_size": Decimal("0.01"), "status_as_of": NOW,
        "verified_reference_price": Decimal("100"),
    }
    values.update(changes)
    return MarketComplianceState(**values)  # type: ignore[arg-type]


def _risk(approved: bool = True) -> RiskDecision:
    return RiskDecision(approved, "test", 20, 10.0 if approved else 0.0, True, True, ())


def _gfv(quantity: str = "5", approved: bool = True, safe: str = "20") -> SellComplianceDecision:
    amount = Decimal(quantity)
    return SellComplianceDecision(approved, "test", amount, Decimal(safe), Decimal("0"), None, ())


def _evaluate(order: ProposedOrder | None = None, account: AccountComplianceState | None = None,
              market: MarketComplianceState | None = None, **kwargs: object):
    return evaluate_order_compliance(
        order or _order(), account or _account(), market or _market(),
        kwargs.pop("limits", DEFAULT_LIMITS),
        kwargs.pop("kill_switch", KillSwitchState(False, "", None, "")),
        risk_decision=kwargs.pop("risk_decision", _risk()),
        gfv_decision=kwargs.pop("gfv_decision", None),
    )


def test_valid_regular_buy() -> None:
    assert _evaluate().approved


def test_valid_regular_sell() -> None:
    assert _evaluate(_order(side=OrderSide.SELL)).approved


def test_cash_sell_requires_approved_matching_gfv() -> None:
    order = _order(side=OrderSide.SELL)
    account = _account(account_type=AccountType.CASH)
    assert not _evaluate(order, account).approved
    assert not _evaluate(order, account, gfv_decision=_gfv(approved=False)).approved
    assert not _evaluate(order, account, gfv_decision=_gfv(quantity="4")).approved
    assert _evaluate(order, account, gfv_decision=_gfv()).approved


def test_gfv_safe_partial_exceeded_is_not_resized() -> None:
    order = _order(side=OrderSide.SELL, quantity=Decimal("5"))
    decision = _evaluate(order, _account(account_type=AccountType.CASH), gfv_decision=_gfv(safe="4"))
    assert not decision.approved and decision.maximum_compliant_quantity == Decimal("4")
    assert order.quantity == Decimal("5")


def test_kill_switch_cannot_be_bypassed() -> None:
    state = KillSwitchState(True, "Emergency", NOW, "admin")
    assert not _evaluate(kill_switch=state).approved
    assert not _evaluate(kill_switch=None).approved


def test_unapproved_or_missing_risk_rejects() -> None:
    assert not _evaluate(risk_decision=_risk(False)).approved
    assert not _evaluate(risk_decision=None).approved


@pytest.mark.parametrize(
    "realized, unrealized",
    [("-500", "0"), ("-300", "-200"), ("-250", "-251")],
)
def test_daily_loss_amount_uses_combined_positive_magnitude(realized: str, unrealized: str) -> None:
    decision = _evaluate(account=_account(current_daily_realized_pnl=Decimal(realized), current_daily_unrealized_pnl=Decimal(unrealized)))
    assert not decision.approved and "daily_loss" in decision.checks_failed


def test_daily_loss_percentage_reached() -> None:
    limits = replace(DEFAULT_LIMITS, maximum_daily_loss_amount=Decimal("9999"), maximum_daily_loss_percent=Decimal("2"))
    assert not _evaluate(account=_account(current_daily_realized_pnl=Decimal("-200")), limits=limits).approved


def test_trade_count_limit() -> None:
    assert not _evaluate(account=_account(trades_executed_today=10)).approved


@pytest.mark.parametrize(
    "market",
    [
        _market(market_status=MarketStatus.CLOSED), _market(market_status=MarketStatus.HALTED),
        _market(market_status=MarketStatus.CIRCUIT_BREAKER), _market(market_status=MarketStatus.UNKNOWN),
        _market(symbol_status=SymbolStatus.NOT_TRADABLE), _market(symbol_status=SymbolStatus.UNKNOWN),
        _market(status_as_of=NOW - timedelta(seconds=31)),
        _market(regular_session_open=NOW + timedelta(hours=1)),
    ],
)
def test_invalid_market_conditions_fail_closed(market: MarketComplianceState) -> None:
    assert not _evaluate(market=market).approved


def test_extended_hours_limit_order() -> None:
    limits = replace(DEFAULT_LIMITS, allow_extended_hours=True)
    order = _order(requested_session=TradingSession.EXTENDED_HOURS)
    assert _evaluate(order, limits=limits).approved
    assert not _evaluate(order).approved


def test_extended_hours_market_order_rejected_by_default() -> None:
    limits = replace(DEFAULT_LIMITS, allow_extended_hours=True)
    order = _order(order_type=OrderType.MARKET, limit_price=None, requested_session=TradingSession.EXTENDED_HOURS)
    assert not _evaluate(order, limits=limits).approved


def test_duplicate_request_and_fingerprint() -> None:
    order = _order()
    assert not _evaluate(order, _account(open_order_request_ids=(order.request_id,))).approved
    assert not _evaluate(order, _account(open_order_fingerprints=(order_fingerprint(order),))).approved


def test_sell_above_position_and_short_sale_rejected() -> None:
    assert not _evaluate(_order(side=OrderSide.SELL, quantity=Decimal("21"))).approved
    assert not _evaluate(_order(side=OrderSide.SELL), _account(current_symbol_position_quantity=Decimal("0"))).approved


def test_concentration_and_gross_exposure_limits() -> None:
    assert _evaluate(_order(quantity=Decimal("1"))).approved
    assert not _evaluate(_order(quantity=Decimal("10"))).approved
    limits = replace(DEFAULT_LIMITS, maximum_position_percent=Decimal("100"), maximum_gross_exposure_percent=Decimal("10"))
    assert not _evaluate(_order(quantity=Decimal("1")), limits=limits).approved


def test_tick_rules_and_informational_bounds_do_not_reprice() -> None:
    assert _evaluate(_order(quantity=Decimal("1"), limit_price=Decimal("100.01"))).approved
    order = _order(quantity=Decimal("1"), limit_price=Decimal("100.005"))
    decision = _evaluate(order)
    assert not decision.approved
    assert decision.normalized_limit_price is None
    assert decision.lower_valid_tick == Decimal("100.00")
    assert decision.upper_valid_tick == Decimal("100.01")
    assert order.limit_price == Decimal("100.005")


def test_required_prices() -> None:
    assert not _evaluate(_order(limit_price=None)).approved
    assert not _evaluate(_order(order_type=OrderType.STOP, limit_price=None, stop_price=None)).approved
    assert not _evaluate(_order(order_type=OrderType.STOP_LIMIT, stop_price=None)).approved


@pytest.mark.parametrize("quantity", [Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")])
def test_invalid_quantities_fail_closed(quantity: Decimal) -> None:
    assert not _evaluate(_order(quantity=quantity)).approved


def test_symbol_mismatch_and_naive_timestamp() -> None:
    assert not _evaluate(market=_market(symbol="OTHER")).approved
    assert not _evaluate(_order(created_timestamp=datetime(2026, 7, 20, 15))).approved


def test_market_buy_requires_verified_reference_price() -> None:
    order = _order(order_type=OrderType.MARKET, limit_price=None)
    assert not _evaluate(order, market=_market(verified_reference_price=None)).approved


def test_repeated_results_are_deterministic_and_json_safe() -> None:
    first, second = _evaluate(), _evaluate()
    assert first == second
    assert first.to_dict()["maximum_compliant_quantity"] == "10"
