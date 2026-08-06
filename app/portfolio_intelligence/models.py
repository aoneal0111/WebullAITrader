"""Immutable broker-neutral portfolio-intelligence domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


ZERO = Decimal("0")


def _decimal(value: Decimal | str | int | None, name: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{name} must be Decimal-compatible")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _time(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _text(value: str, name: str, *, upper: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    value = value.strip()
    return value.upper() if upper else value


class PositionSide(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class RiskBudgetClassification(StrEnum):
    WITHIN_LIMITS = "Within Limits"
    APPROACHING_LIMIT = "Approaching Limit"
    AT_LIMIT = "At Limit"
    EXCEEDED = "Exceeded"
    UNKNOWN = "Unknown"


@dataclass(frozen=True, slots=True)
class PortfolioAccount:
    account_id: str
    equity: Decimal | None
    cash: Decimal | None
    buying_power: Decimal | None
    currency: str = "USD"

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_id", _text(self.account_id, "account_id"))
        object.__setattr__(self, "currency", _text(self.currency, "currency", upper=True))
        for name in ("equity", "cash", "buying_power"):
            value = _decimal(getattr(self, name), name)
            if value is not None and value < ZERO:
                raise ValueError(f"{name} cannot be negative")
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class PortfolioPosition:
    symbol: str
    quantity: Decimal
    average_entry_price: Decimal | None
    current_mark: Decimal | None
    asset_class: str = "UNKNOWN"
    currency: str = "USD"
    strategy_id: str | None = None
    decision_type: str | None = None
    sector: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol", upper=True))
        object.__setattr__(self, "asset_class", _text(self.asset_class, "asset_class", upper=True))
        object.__setattr__(self, "currency", _text(self.currency, "currency", upper=True))
        quantity = _decimal(self.quantity, "quantity")
        if quantity == ZERO:
            raise ValueError("position quantity must be non-zero")
        object.__setattr__(self, "quantity", quantity)
        for name in ("average_entry_price", "current_mark"):
            value = _decimal(getattr(self, name), name)
            if value is not None and value < ZERO:
                raise ValueError(f"{name} cannot be negative")
            object.__setattr__(self, name, value)
        for name in ("strategy_id", "decision_type", "sector"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _text(value, name))

    @property
    def side(self) -> PositionSide:
        return PositionSide.LONG if self.quantity > ZERO else PositionSide.SHORT

    @property
    def market_value(self) -> Decimal | None:
        return None if self.current_mark is None else self.quantity * self.current_mark

    @property
    def unrealized_pnl(self) -> Decimal | None:
        if self.current_mark is None or self.average_entry_price is None:
            return None
        return (self.current_mark - self.average_entry_price) * self.quantity


@dataclass(frozen=True, slots=True)
class WorkingOrder:
    order_id: str
    symbol: str
    side: OrderSide
    remaining_quantity: Decimal
    price: Decimal | None = None
    asset_class: str = "UNKNOWN"
    strategy_id: str | None = None
    decision_type: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "order_id", _text(self.order_id, "order_id"))
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol", upper=True))
        object.__setattr__(self, "asset_class", _text(self.asset_class, "asset_class", upper=True))
        if not isinstance(self.side, OrderSide):
            object.__setattr__(self, "side", OrderSide(str(self.side).upper()))
        quantity = _decimal(self.remaining_quantity, "remaining_quantity")
        if quantity is None or quantity <= ZERO:
            raise ValueError("remaining_quantity must be positive")
        object.__setattr__(self, "remaining_quantity", quantity)
        price = _decimal(self.price, "price")
        if price is not None and price <= ZERO:
            raise ValueError("price must be positive")
        object.__setattr__(self, "price", price)


@dataclass(frozen=True, slots=True)
class PortfolioFill:
    fill_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    timestamp: datetime
    realized_pnl: Decimal | None = None
    asset_class: str = "UNKNOWN"
    strategy_id: str | None = None
    decision_type: str | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "fill_id", _text(self.fill_id, "fill_id"))
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol", upper=True))
        object.__setattr__(self, "asset_class", _text(self.asset_class, "asset_class", upper=True))
        if not isinstance(self.side, OrderSide):
            object.__setattr__(self, "side", OrderSide(str(self.side).upper()))
        for name in ("quantity", "price"):
            value = _decimal(getattr(self, name), name)
            if value is None or value <= ZERO:
                raise ValueError(f"{name} must be positive")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "realized_pnl", _decimal(self.realized_pnl, "realized_pnl"))
        object.__setattr__(self, "timestamp", _time(self.timestamp, "timestamp"))


@dataclass(frozen=True, slots=True)
class PriceObservation:
    timestamp: datetime
    price: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _time(self.timestamp, "timestamp"))
        price = _decimal(self.price, "price")
        if price is None or price <= ZERO:
            raise ValueError("price must be positive")
        object.__setattr__(self, "price", price)


@dataclass(frozen=True, slots=True)
class EquityObservation:
    timestamp: datetime
    equity: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _time(self.timestamp, "timestamp"))
        equity = _decimal(self.equity, "equity")
        if equity is None or equity < ZERO:
            raise ValueError("equity cannot be negative")
        object.__setattr__(self, "equity", equity)


@dataclass(frozen=True, slots=True)
class ExposureSummary:
    gross_exposure: Decimal | None
    net_exposure: Decimal | None
    long_exposure: Decimal | None
    short_exposure: Decimal | None
    cash_percentage: Decimal | None
    buying_power_utilization: Decimal | None
    position_weights: tuple[tuple[str, Decimal | None], ...]
    largest_position_weight: Decimal | None
    top_five_concentration: Decimal | None
    open_positions: int
    pending_order_exposure: Decimal | None
    gross_exposure_after_orders: Decimal | None
    net_exposure_after_orders: Decimal | None


@dataclass(frozen=True, slots=True)
class ConcentrationSummary:
    largest_symbol_allocation: Decimal | None
    top_three_allocation: Decimal | None
    top_five_allocation: Decimal | None
    hhi: Decimal | None
    long_short_imbalance: Decimal | None
    strategy_concentration: tuple[tuple[str, Decimal], ...]
    asset_class_concentration: tuple[tuple[str, Decimal], ...]
    sector_concentration: tuple[tuple[str, Decimal], ...] | None


@dataclass(frozen=True, slots=True)
class CorrelationPair:
    first_symbol: str
    second_symbol: str
    correlation: Decimal
    observations: int


@dataclass(frozen=True, slots=True)
class CorrelationSummary:
    highest_absolute_pair: CorrelationPair | None
    average_pair_correlation: Decimal | None
    highly_correlated_pairs: tuple[CorrelationPair, ...]
    correlated_portfolio_percentage: Decimal | None
    eligible_pairs: int
    excluded_pairs: int


@dataclass(frozen=True, slots=True)
class PerformanceSummary:
    daily_realized_pnl: Decimal | None
    daily_unrealized_pnl: Decimal | None
    daily_total_pnl: Decimal | None
    cumulative_realized_pnl: Decimal | None
    gross_profit: Decimal | None
    gross_loss: Decimal | None
    win_rate: Decimal | None
    loss_rate: Decimal | None
    profit_factor: Decimal | None
    average_win: Decimal | None
    average_loss: Decimal | None
    expectancy: Decimal | None
    average_holding_period_seconds: Decimal | None
    maximum_drawdown: Decimal | None
    current_drawdown: Decimal | None
    return_on_equity: Decimal | None
    trade_count: int


@dataclass(frozen=True, slots=True)
class AttributionEntry:
    key: str
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_pnl: Decimal


@dataclass(frozen=True, slots=True)
class AttributionSummary:
    by_symbol: tuple[AttributionEntry, ...]
    by_strategy: tuple[AttributionEntry, ...]
    by_decision_type: tuple[AttributionEntry, ...]
    by_asset_class: tuple[AttributionEntry, ...]
    by_session: tuple[AttributionEntry, ...]
    realized_pnl: Decimal | None
    unrealized_pnl: Decimal | None


@dataclass(frozen=True, slots=True)
class RiskBudgetMetric:
    name: str
    current: Decimal | int | None
    limit: Decimal | int | None
    classification: RiskBudgetClassification


@dataclass(frozen=True, slots=True)
class PortfolioRiskBudgetStatus:
    overall: RiskBudgetClassification
    metrics: tuple[RiskBudgetMetric, ...]


@dataclass(frozen=True, slots=True)
class PortfolioIntelligenceSnapshot:
    account: PortfolioAccount
    generated_at: datetime
    positions: tuple[PortfolioPosition, ...]
    working_orders: tuple[WorkingOrder, ...]
    realized_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    total_pnl: Decimal | None
    exposure: ExposureSummary
    concentration: ConcentrationSummary
    correlation: CorrelationSummary
    performance: PerformanceSummary
    attribution: AttributionSummary
    risk_budget: PortfolioRiskBudgetStatus
    observations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_at", _time(self.generated_at, "generated_at"))


@dataclass(frozen=True, slots=True)
class PortfolioIntelligenceInput:
    account: PortfolioAccount
    positions: tuple[PortfolioPosition, ...] = ()
    working_orders: tuple[WorkingOrder, ...] = ()
    fills: tuple[PortfolioFill, ...] = ()
    price_history: Mapping[str, tuple[PriceObservation, ...]] = field(default_factory=dict)
    equity_history: tuple[EquityObservation, ...] = ()
    generated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.account, PortfolioAccount):
            raise TypeError("account must be PortfolioAccount")
        for name, expected in (("positions", PortfolioPosition), ("working_orders", WorkingOrder), ("fills", PortfolioFill), ("equity_history", EquityObservation)):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(not isinstance(item, expected) for item in values):
                raise TypeError(f"{name} must be an immutable {expected.__name__} tuple")
        history = {str(symbol).strip().upper(): tuple(values) for symbol, values in self.price_history.items()}
        if any(any(not isinstance(item, PriceObservation) for item in values) for values in history.values()):
            raise TypeError("price_history values must contain PriceObservation")
        object.__setattr__(self, "price_history", MappingProxyType(history))
        if self.generated_at is not None:
            object.__setattr__(self, "generated_at", _time(self.generated_at, "generated_at"))
