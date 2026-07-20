from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR
from datetime import timedelta

from app.compliance.models import AccountType, SellComplianceDecision
from app.order_compliance.kill_switch import KillSwitchState, kill_switch_failure
from app.order_compliance.market_hours import validate_market_and_session
from app.order_compliance.models import (
    AccountComplianceState, ComplianceLimits, MarketComplianceState, OrderComplianceDecision,
    OrderSide, OrderType, ProposedOrder, TradingSession,
)
from app.order_compliance.price_rules import PriceValidation, validate_prices
from app.risk.models import RiskDecision

ZERO = Decimal("0")


def evaluate_order_compliance(
    proposed_order: ProposedOrder,
    account_state: AccountComplianceState,
    market_state: MarketComplianceState,
    limits: ComplianceLimits,
    kill_switch: KillSwitchState,
    *,
    gfv_decision: SellComplianceDecision | None = None,
    risk_decision: RiskDecision | None = None,
) -> OrderComplianceDecision:
    """Evaluate, but never transform or execute, an immutable order proposal."""
    passed: list[str] = []
    failed: list[str] = []
    warnings: list[str] = []
    request_id = proposed_order.request_id if isinstance(proposed_order, ProposedOrder) and isinstance(proposed_order.request_id, str) else ""
    if not all((
        isinstance(proposed_order, ProposedOrder), isinstance(account_state, AccountComplianceState),
        isinstance(market_state, MarketComplianceState), isinstance(limits, ComplianceLimits),
    )):
        return _decision(False, "Rejected: one or more input objects are missing or malformed.", request_id,
                         (), ("input_types",), ("Safety-critical input types failed validation.",), None, None)
    _check(passed, failed, "input_types", True)

    base_valid = _valid_order(proposed_order) and _valid_account(account_state) and _valid_limits(limits)
    _check(passed, failed, "core_fields", base_valid)

    kill_failure = kill_switch_failure(kill_switch)
    _check(passed, failed, "kill_switch", kill_failure is None)
    if kill_failure:
        warnings.append(kill_failure)

    risk_approved = isinstance(risk_decision, RiskDecision) and risk_decision.approved
    _check(passed, failed, "risk_approval", risk_approved)

    is_cash_sell = proposed_order.side is OrderSide.SELL and account_state.account_type is AccountType.CASH
    gfv_approved = not is_cash_sell or (
        isinstance(gfv_decision, SellComplianceDecision)
        and gfv_decision.approved
        and gfv_decision.requested_quantity == proposed_order.quantity
    )
    _check(passed, failed, "gfv_approval", gfv_approved)

    price_result = validate_prices(proposed_order, market_state.price_tick_size)
    price = _proposal_price(proposed_order, market_state)
    maximum_quantity = _maximum_quantity(proposed_order, account_state, risk_decision, gfv_decision, price) if base_valid else None
    within_upstream = (
        _positive_decimal(proposed_order.quantity)
        and maximum_quantity is not None
        and maximum_quantity.is_finite()
        and proposed_order.quantity <= maximum_quantity
    )
    _check(passed, failed, "upstream_quantity", within_upstream)

    timestamps_fresh = _fresh_order_state(proposed_order, account_state, limits)
    _check(passed, failed, "account_freshness", timestamps_fresh)

    market_failures = validate_market_and_session(proposed_order, market_state, limits, account_state.current_timestamp)
    freshness_failures = [item for item in market_failures if _market_failure_label(item) == "market_freshness"]
    _check(passed, failed, "market_freshness", not freshness_failures)
    warnings.extend(freshness_failures)
    _check(passed, failed, "symbol_match", proposed_order.symbol.strip().upper() == market_state.symbol.strip().upper())
    for label in ("symbol_tradable", "market_status", "market_session"):
        relevant = [item for item in market_failures if _market_failure_label(item) == label]
        _check(passed, failed, label, not relevant)
        warnings.extend(relevant)

    _check(passed, failed, "price_rules", not price_result.failures)
    warnings.extend(price_result.failures)
    _check(passed, failed, "duplicate_request_id", proposed_order.request_id not in account_state.open_order_request_ids)
    fingerprint = order_fingerprint(proposed_order) if base_valid else ""
    _check(passed, failed, "duplicate_fingerprint", base_valid and fingerprint not in account_state.open_order_fingerprints)

    sell_supported = (
        proposed_order.side is not OrderSide.SELL
        or (
            _positive_decimal(proposed_order.quantity)
            and isinstance(account_state.current_symbol_position_quantity, Decimal)
            and account_state.current_symbol_position_quantity.is_finite()
            and proposed_order.quantity <= account_state.current_symbol_position_quantity
        )
    )
    _check(passed, failed, "long_position", sell_supported)
    _check(passed, failed, "trade_count", account_state.trades_executed_today < limits.maximum_trades_per_day)

    daily_loss_ok = _daily_loss_ok(account_state, limits)
    _check(passed, failed, "daily_loss", daily_loss_ok)

    concentration_ok, gross_ok = _exposure_checks(proposed_order, account_state, limits, price)
    _check(passed, failed, "position_concentration", concentration_ok)
    _check(passed, failed, "gross_exposure", gross_ok)

    approved = not failed
    if not approved:
        warnings.append("Informational quantities and tick bounds require a new explicit request; this proposal was not changed.")
    return OrderComplianceDecision(
        approved,
        "Approved for paper-execution simulation only." if approved else "Rejected: one or more mandatory compliance checks failed.",
        proposed_order.request_id, tuple(passed), tuple(failed), tuple(warnings), maximum_quantity,
        price_result.normalized_limit_price, price_result.normalized_stop_price,
        price_result.lower_valid_tick, price_result.upper_valid_tick,
    )


def order_fingerprint(order: ProposedOrder) -> str:
    def decimal_text(value: Decimal | None) -> str:
        return "" if value is None else format(value.normalize(), "f")
    return "|".join((order.symbol.strip().upper(), order.side.value, order.order_type.value,
                     decimal_text(order.quantity), decimal_text(order.limit_price), decimal_text(order.stop_price),
                     order.requested_session.value))


def _valid_order(order: ProposedOrder) -> bool:
    return (
        bool(order.request_id.strip()) and bool(order.symbol.strip())
        and isinstance(order.side, OrderSide) and isinstance(order.order_type, OrderType)
        and isinstance(order.requested_session, TradingSession)
        and _positive_decimal(order.quantity)
        and order.created_timestamp.tzinfo is not None
    )


def _valid_account(state: AccountComplianceState) -> bool:
    decimals = (state.account_equity, state.current_daily_realized_pnl, state.current_daily_unrealized_pnl,
                state.current_symbol_position_quantity, state.current_symbol_market_value, state.current_total_gross_exposure)
    return (
        isinstance(state.account_type, AccountType) and all(isinstance(v, Decimal) and v.is_finite() for v in decimals)
        and state.account_equity > ZERO and state.current_symbol_position_quantity >= ZERO
        and state.current_symbol_market_value >= ZERO and state.current_total_gross_exposure >= ZERO
        and isinstance(state.trades_executed_today, int) and not isinstance(state.trades_executed_today, bool)
        and state.trades_executed_today >= 0 and state.current_timestamp.tzinfo is not None
        and isinstance(state.open_order_request_ids, tuple)
        and all(isinstance(value, str) for value in state.open_order_request_ids)
        and isinstance(state.open_order_fingerprints, tuple)
        and all(isinstance(value, str) for value in state.open_order_fingerprints)
    )


def _valid_limits(value: ComplianceLimits) -> bool:
    decimals = (value.maximum_daily_loss_amount, value.maximum_daily_loss_percent,
                value.maximum_position_percent, value.maximum_gross_exposure_percent)
    return (
        all(_positive_decimal(item) for item in decimals)
        and isinstance(value.maximum_trades_per_day, int) and not isinstance(value.maximum_trades_per_day, bool)
        and value.maximum_trades_per_day > 0
        and isinstance(value.maximum_market_status_age_seconds, int)
        and not isinstance(value.maximum_market_status_age_seconds, bool)
        and value.maximum_market_status_age_seconds >= 0
        and isinstance(value.allow_extended_hours, bool)
        and isinstance(value.allow_market_orders_in_extended_hours, bool)
    )


def _daily_loss_ok(state: AccountComplianceState, limits: ComplianceLimits) -> bool:
    values = (
        state.current_daily_realized_pnl, state.current_daily_unrealized_pnl,
        state.account_equity, limits.maximum_daily_loss_amount, limits.maximum_daily_loss_percent,
    )
    if not all(isinstance(value, Decimal) and value.is_finite() for value in values):
        return False
    if state.account_equity <= ZERO:
        return False
    combined_pnl = state.current_daily_realized_pnl + state.current_daily_unrealized_pnl
    loss_magnitude = -combined_pnl if combined_pnl < ZERO else ZERO
    loss_percent = loss_magnitude / state.account_equity * Decimal("100")
    return loss_magnitude < limits.maximum_daily_loss_amount and loss_percent < limits.maximum_daily_loss_percent


def _fresh_order_state(order: ProposedOrder, state: AccountComplianceState, limits: ComplianceLimits) -> bool:
    if order.created_timestamp.tzinfo is None or state.current_timestamp.tzinfo is None:
        return False
    age = state.current_timestamp - order.created_timestamp
    return timedelta(0) <= age <= timedelta(seconds=limits.maximum_market_status_age_seconds)


def _proposal_price(order: ProposedOrder, market: MarketComplianceState) -> Decimal | None:
    if order.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
        return order.limit_price if _positive_decimal(order.limit_price) else None
    if order.order_type is OrderType.STOP:
        return order.stop_price if _positive_decimal(order.stop_price) else None
    return market.verified_reference_price if _positive_decimal(market.verified_reference_price) else None


def _maximum_quantity(order: ProposedOrder, account: AccountComplianceState, risk: RiskDecision | None,
                      gfv: SellComplianceDecision | None, price: Decimal | None) -> Decimal | None:
    if order.side is OrderSide.SELL:
        maximum = account.current_symbol_position_quantity
        if account.account_type is AccountType.CASH:
            if not isinstance(gfv, SellComplianceDecision):
                return ZERO
            maximum = min(maximum, gfv.safe_sell_quantity)
        return maximum
    if not isinstance(risk, RiskDecision) or price is None:
        return ZERO
    percent = risk.max_position_percent
    if not percent.is_finite() or percent < ZERO:
        return ZERO
    return ((account.account_equity * percent / Decimal("100")) / price).to_integral_value(rounding=ROUND_FLOOR)


def _exposure_checks(order: ProposedOrder, account: AccountComplianceState, limits: ComplianceLimits,
                     price: Decimal | None) -> tuple[bool, bool]:
    if not _positive_decimal(order.quantity) or not _positive_decimal(account.account_equity):
        return False, False
    if order.side is OrderSide.SELL:
        supported = order.quantity <= account.current_symbol_position_quantity
        return supported, supported
    if price is None:
        return False, False
    notional = order.quantity * price
    position_percent = (account.current_symbol_market_value + notional) / account.account_equity * Decimal("100")
    gross_percent = (account.current_total_gross_exposure + notional) / account.account_equity * Decimal("100")
    return position_percent <= limits.maximum_position_percent, gross_percent <= limits.maximum_gross_exposure_percent


def _market_failure_label(message: str) -> str:
    if "stale or future-dated" in message:
        return "market_freshness"
    if message.startswith("Symbol"):
        return "symbol_tradable"
    if message.startswith("Market status"):
        return "market_status"
    return "market_session"


def _positive_decimal(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > ZERO


def _check(passed: list[str], failed: list[str], name: str, condition: bool) -> None:
    (passed if condition else failed).append(name)


def _decision(approved: bool, reason: str, request_id: str, passed: tuple[str, ...], failed: tuple[str, ...],
              warnings: tuple[str, ...], maximum: Decimal | None, prices: PriceValidation | None) -> OrderComplianceDecision:
    return OrderComplianceDecision(approved, reason, request_id, passed, failed, warnings, maximum,
                                   None if prices is None else prices.normalized_limit_price,
                                   None if prices is None else prices.normalized_stop_price,
                                   None if prices is None else prices.lower_valid_tick,
                                   None if prices is None else prices.upper_valid_tick)
