from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.compliance import (
    AccountType,
    FundingSource,
    PurchaseLot,
    SecurityType,
    SettlementCalendar,
    SettlementLedger,
    evaluate_sell_compliance,
)

NOW = datetime(2026, 7, 20, 14, tzinfo=UTC)


def _lot(
    quantity: str,
    source: FundingSource,
    settlement: date | None = None,
    symbol: str = "TEST",
) -> PurchaseLot:
    amount = Decimal(quantity)
    return PurchaseLot(symbol, amount, NOW, source, settlement, amount)


def _evaluate(quantity: str, lots: list[PurchaseLot], account: AccountType | None = AccountType.CASH):
    return evaluate_sell_compliance("TEST", Decimal(quantity), account, NOW, lots)


def test_settled_cash_purchase_is_safe() -> None:
    result = _evaluate("10", [_lot("10", FundingSource.SETTLED_CASH)])
    assert result.approved and result.safe_sell_quantity == Decimal("10")


def test_unsettled_proceeds_purchase_is_restricted() -> None:
    result = _evaluate("10", [_lot("10", FundingSource.UNSETTLED_SALE_PROCEEDS, date(2026, 7, 21))])
    assert not result.approved and result.safe_sell_quantity == 0
    assert result.next_eligible_sell_date == date(2026, 7, 21)


def test_mixed_funding_reports_safe_partial_without_resizing_request() -> None:
    lots = [_lot("4", FundingSource.SETTLED_CASH), _lot("6", FundingSource.UNSETTLED_SALE_PROCEEDS, date(2026, 7, 21))]
    result = _evaluate("8", lots)
    assert not result.approved
    assert result.requested_quantity == Decimal("8")
    assert result.safe_sell_quantity == Decimal("4")
    assert result.restricted_quantity == Decimal("4")
    assert any("not automatically reduced" in warning for warning in result.warnings)


def test_new_explicit_partial_sale_within_safe_quantity_is_approved() -> None:
    lots = [_lot("4", FundingSource.SETTLED_CASH), _lot("6", FundingSource.UNSETTLED_SALE_PROCEEDS, date(2026, 7, 21))]
    assert _evaluate("4", lots).approved


def test_requested_sale_exceeding_position_is_rejected() -> None:
    result = _evaluate("11", [_lot("10", FundingSource.SETTLED_CASH)])
    assert not result.approved and result.safe_sell_quantity == Decimal("10")


def test_excess_request_restricted_quantity_uses_safe_not_total_quantity() -> None:
    lots = [_lot("4", FundingSource.SETTLED_CASH), _lot("6", FundingSource.UNKNOWN)]
    result = _evaluate("11", lots)
    assert result.safe_sell_quantity == Decimal("4")
    assert result.restricted_quantity == Decimal("7")


def test_sale_after_funding_settlement_is_safe() -> None:
    result = _evaluate("5", [_lot("5", FundingSource.UNSETTLED_SALE_PROCEEDS, date(2026, 7, 20))])
    assert result.approved


def test_friday_trade_settles_monday() -> None:
    calendar = SettlementCalendar()
    assert calendar.settlement_date(date(2026, 7, 17)) == date(2026, 7, 20)


def test_market_holiday_delays_settlement() -> None:
    calendar = SettlementCalendar(frozenset({date(2026, 7, 20)}))
    assert calendar.settlement_date(date(2026, 7, 17)) == date(2026, 7, 21)


def test_ledger_records_t_plus_one_sale_funding() -> None:
    ledger = SettlementLedger().record_purchase_funded_by_sale(
        symbol="test", quantity=Decimal("2"), purchase_timestamp=NOW,
        funding_sale_trade_date=date(2026, 7, 17), security_type=SecurityType.STOCK,
        calendar=SettlementCalendar(),
    )
    assert ledger.lots[0].funding_settlement_date == date(2026, 7, 20)


def test_missing_account_type_fails_closed() -> None:
    assert not _evaluate("1", [_lot("1", FundingSource.SETTLED_CASH)], None).approved


def test_unknown_funding_is_always_restricted() -> None:
    result = _evaluate("1", [_lot("1", FundingSource.UNKNOWN, date(2026, 7, 1))])
    assert not result.approved and result.next_eligible_sell_date is None


@pytest.mark.parametrize("quantity", [Decimal("0"), Decimal("-1"), Decimal("NaN"), "1"])
def test_malformed_quantities_fail_closed(quantity: object) -> None:
    result = evaluate_sell_compliance("TEST", quantity, AccountType.CASH, NOW, [_lot("1", FundingSource.SETTLED_CASH)])  # type: ignore[arg-type]
    assert not result.approved and result.safe_sell_quantity == 0


def test_margin_account_is_distinguished() -> None:
    result = _evaluate("1", [_lot("1", FundingSource.UNKNOWN)], AccountType.MARGIN)
    assert result.approved


def test_missing_settlement_date_fails_closed_for_lot() -> None:
    result = _evaluate("1", [_lot("1", FundingSource.PROVISIONAL_DEPOSIT)])
    assert not result.approved and result.next_eligible_sell_date is None


def test_repeated_results_are_deterministic() -> None:
    lots = [_lot("2", FundingSource.UNSETTLED_SALE_PROCEEDS, date(2026, 7, 21))]
    assert _evaluate("1", lots) == _evaluate("1", lots)
