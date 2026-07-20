from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

from app.compliance.models import (
    AccountType,
    FundingSource,
    PurchaseLot,
    SellComplianceDecision,
)

ZERO = Decimal("0")


def evaluate_sell_compliance(
    requested_symbol: str,
    requested_sell_quantity: Decimal,
    account_type: AccountType | None,
    current_timestamp: datetime,
    relevant_position_lots: Sequence[PurchaseLot],
) -> SellComplianceDecision:
    """Evaluate GFV safety without creating, resizing, or submitting an order."""
    malformed = _validate_request(
        requested_symbol, requested_sell_quantity, account_type, current_timestamp, relevant_position_lots
    )
    if malformed:
        return _reject_malformed(requested_sell_quantity, malformed)

    symbol = requested_symbol.strip().upper()
    matching = tuple(lot for lot in relevant_position_lots if lot.symbol.strip().upper() == symbol)
    total_quantity = sum((lot.remaining_quantity for lot in matching), ZERO)
    if requested_sell_quantity > total_quantity:
        safe_quantity = (
            total_quantity
            if account_type is AccountType.MARGIN
            else _safe_cash_quantity(matching, current_timestamp)
        )
        return SellComplianceDecision(
            False,
            "Rejected: requested quantity exceeds the recorded remaining position.",
            requested_sell_quantity,
            safe_quantity,
            requested_sell_quantity - safe_quantity,
            None,
            ("A new explicit request within the recorded position is required.",),
        )

    if account_type is AccountType.MARGIN:
        return SellComplianceDecision(
            True,
            "Approved: cash-account GFV restrictions do not apply to the identified margin account.",
            requested_sell_quantity,
            total_quantity,
            ZERO,
            None,
            (),
        )

    safe_quantity = _safe_cash_quantity(matching, current_timestamp)
    restricted = max(ZERO, requested_sell_quantity - safe_quantity)
    approved = requested_sell_quantity <= safe_quantity
    next_date = None if approved else _date_when_quantity_is_safe(
        matching, current_timestamp, requested_sell_quantity
    )
    if approved:
        return SellComplianceDecision(
            True,
            "Approved: the explicit requested quantity is supported by GFV-safe lots.",
            requested_sell_quantity,
            safe_quantity,
            ZERO,
            None,
            (),
        )
    warnings = [
        "The original sell request remains rejected and was not automatically reduced.",
        "safe_sell_quantity is informational; submit a new explicit request to use it.",
    ]
    if next_date is None:
        warnings.append("Eligibility cannot be determined for one or more restricted lots.")
    return SellComplianceDecision(
        False,
        "Rejected: requested quantity exceeds the quantity currently safe from a Good Faith Violation.",
        requested_sell_quantity,
        safe_quantity,
        restricted,
        next_date,
        tuple(warnings),
    )


def _safe_cash_quantity(lots: Sequence[PurchaseLot], now: datetime) -> Decimal:
    return sum((lot.remaining_quantity for lot in lots if _lot_is_safe(lot, now.date())), ZERO)


def _lot_is_safe(lot: PurchaseLot, current_date: date) -> bool:
    if lot.funding_source is FundingSource.SETTLED_CASH:
        return True
    if lot.funding_source is FundingSource.UNKNOWN:
        return False
    return lot.funding_settlement_date is not None and current_date >= lot.funding_settlement_date


def _date_when_quantity_is_safe(
    lots: Sequence[PurchaseLot], now: datetime, requested: Decimal
) -> date | None:
    available = _safe_cash_quantity(lots, now)
    if available >= requested:
        return now.date()
    dated_lots = sorted(
        (
            lot
            for lot in lots
            if not _lot_is_safe(lot, now.date())
            and lot.funding_source is not FundingSource.UNKNOWN
            and lot.funding_settlement_date is not None
        ),
        key=lambda lot: lot.funding_settlement_date or date.max,
    )
    for lot in dated_lots:
        available += lot.remaining_quantity
        if available >= requested:
            return lot.funding_settlement_date
    return None


def _validate_request(
    symbol: object,
    quantity: object,
    account_type: object,
    timestamp: object,
    lots: object,
) -> tuple[str, ...]:
    errors: list[str] = []
    if not isinstance(symbol, str) or not symbol.strip():
        errors.append("Symbol is missing or malformed.")
    if not isinstance(quantity, Decimal) or not quantity.is_finite() or quantity <= 0:
        errors.append("Requested quantity must be a finite positive Decimal.")
    if account_type not in (AccountType.CASH, AccountType.MARGIN):
        errors.append("Account type is missing or unknown.")
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        errors.append("Current timestamp must be timezone-aware.")
    if not isinstance(lots, Sequence) or isinstance(lots, (str, bytes)):
        errors.append("Position lots are missing or malformed.")
    elif any(not _valid_lot(lot) for lot in lots):
        errors.append("One or more position lots are malformed or uncertain.")
    return tuple(errors)


def _valid_lot(lot: object) -> bool:
    return (
        isinstance(lot, PurchaseLot)
        and isinstance(lot.funding_source, FundingSource)
        and isinstance(lot.quantity, Decimal)
        and lot.quantity.is_finite()
        and lot.quantity > 0
        and isinstance(lot.remaining_quantity, Decimal)
        and lot.remaining_quantity.is_finite()
        and ZERO <= lot.remaining_quantity <= lot.quantity
        and isinstance(lot.purchase_timestamp, datetime)
        and lot.purchase_timestamp.tzinfo is not None
    )


def _reject_malformed(quantity: object, warnings: tuple[str, ...]) -> SellComplianceDecision:
    reported = quantity if isinstance(quantity, Decimal) and quantity.is_finite() and quantity >= 0 else ZERO
    return SellComplianceDecision(
        False,
        "Rejected: settlement information is missing, malformed, or uncertain.",
        reported,
        ZERO,
        reported,
        None,
        warnings,
    )
