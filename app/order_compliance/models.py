from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.compliance.models import AccountType


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class TradingSession(StrEnum):
    REGULAR = "REGULAR"
    EXTENDED_HOURS = "EXTENDED_HOURS"


class MarketStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    HALTED = "HALTED"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    UNKNOWN = "UNKNOWN"


class SymbolStatus(StrEnum):
    TRADABLE = "TRADABLE"
    NOT_TRADABLE = "NOT_TRADABLE"
    HALTED = "HALTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ProposedOrder:
    """Advisory proposal only; this object has no execution behavior."""

    request_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    requested_session: TradingSession
    created_timestamp: datetime


@dataclass(frozen=True, slots=True)
class AccountComplianceState:
    account_type: AccountType
    account_equity: Decimal
    current_daily_realized_pnl: Decimal
    current_daily_unrealized_pnl: Decimal
    trades_executed_today: int
    open_order_request_ids: tuple[str, ...]
    open_order_fingerprints: tuple[str, ...]
    current_symbol_position_quantity: Decimal
    current_symbol_market_value: Decimal
    current_total_gross_exposure: Decimal
    current_timestamp: datetime


@dataclass(frozen=True, slots=True)
class MarketComplianceState:
    symbol: str
    market_status: MarketStatus
    symbol_status: SymbolStatus
    regular_session_open: datetime
    regular_session_close: datetime
    extended_session_open: datetime
    extended_session_close: datetime
    price_tick_size: Decimal | None
    status_as_of: datetime
    verified_reference_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ComplianceLimits:
    maximum_daily_loss_amount: Decimal
    maximum_daily_loss_percent: Decimal
    maximum_trades_per_day: int
    maximum_position_percent: Decimal
    maximum_gross_exposure_percent: Decimal
    maximum_market_status_age_seconds: int
    allow_extended_hours: bool
    allow_market_orders_in_extended_hours: bool


@dataclass(frozen=True, slots=True)
class OrderComplianceDecision:
    approved: bool
    approval_reason: str
    request_id: str
    checks_passed: tuple[str, ...]
    checks_failed: tuple[str, ...]
    warnings: tuple[str, ...]
    maximum_compliant_quantity: Decimal | None
    normalized_limit_price: Decimal | None
    normalized_stop_price: Decimal | None
    lower_valid_tick: Decimal | None
    upper_valid_tick: Decimal | None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for field in (
            "maximum_compliant_quantity", "normalized_limit_price", "normalized_stop_price",
            "lower_valid_tick", "upper_valid_tick",
        ):
            value = result[field]
            result[field] = format(value, "f") if value is not None else None
        for field in ("checks_passed", "checks_failed", "warnings"):
            result[field] = list(result[field])
        return result
