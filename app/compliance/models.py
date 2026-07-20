from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class AccountType(StrEnum):
    CASH = "CASH"
    MARGIN = "MARGIN"


class FundingSource(StrEnum):
    SETTLED_CASH = "SETTLED_CASH"
    UNSETTLED_SALE_PROCEEDS = "UNSETTLED_SALE_PROCEEDS"
    PROVISIONAL_DEPOSIT = "PROVISIONAL_DEPOSIT"
    UNKNOWN = "UNKNOWN"


class SecurityType(StrEnum):
    STOCK = "STOCK"
    OPTION = "OPTION"


@dataclass(frozen=True, slots=True)
class PurchaseLot:
    symbol: str
    quantity: Decimal
    purchase_timestamp: datetime
    funding_source: FundingSource
    funding_settlement_date: date | None
    remaining_quantity: Decimal

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol must not be empty")
        if not _positive_finite(self.quantity):
            raise ValueError("quantity must be finite and positive")
        if not _nonnegative_finite(self.remaining_quantity):
            raise ValueError("remaining_quantity must be finite and non-negative")
        if self.remaining_quantity > self.quantity:
            raise ValueError("remaining_quantity cannot exceed quantity")
        if self.purchase_timestamp.tzinfo is None:
            raise ValueError("purchase_timestamp must include a timezone")


@dataclass(frozen=True, slots=True)
class SellComplianceDecision:
    approved: bool
    approval_reason: str
    requested_quantity: Decimal
    safe_sell_quantity: Decimal
    restricted_quantity: Decimal
    next_eligible_sell_date: date | None
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.approval_reason.strip():
            raise ValueError("approval_reason must not be empty")
        for value in (self.requested_quantity, self.safe_sell_quantity, self.restricted_quantity):
            if not _nonnegative_finite(value):
                raise ValueError("decision quantities must be finite and non-negative")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["requested_quantity"] = str(self.requested_quantity)
        result["safe_sell_quantity"] = str(self.safe_sell_quantity)
        result["restricted_quantity"] = str(self.restricted_quantity)
        if self.next_eligible_sell_date is not None:
            result["next_eligible_sell_date"] = self.next_eligible_sell_date.isoformat()
        result["warnings"] = list(self.warnings)
        return result


def _positive_finite(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _nonnegative_finite(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= 0
