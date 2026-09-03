from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.order_compliance.models import ProposedOrder


class ExecutionStatus(StrEnum):
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    NOT_FILLED = "NOT_FILLED"


class JournalEventType(StrEnum):
    PROPOSAL = "PROPOSAL"
    CANCELLATION = "CANCELLATION"
    EXPIRATION = "EXPIRATION"
    REJECTION = "REJECTION"
    NOT_FILLED = "NOT_FILLED"
    FILL = "FILL"
    PORTFOLIO_CHANGE = "PORTFOLIO_CHANGE"


@dataclass(frozen=True, slots=True)
class PaperExecutionConfig:
    maximum_quote_age_seconds: int


@dataclass(frozen=True, slots=True)
class PaperMarketQuote:
    symbol: str
    bid: Decimal | None
    ask: Decimal | None
    last_price: Decimal | None
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class PaperPosition:
    symbol: str
    quantity: Decimal
    average_cost: Decimal
    current_mark: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class PaperPortfolio:
    initial_cash: Decimal
    cash: Decimal
    positions: tuple[PaperPosition, ...]
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    equity: Decimal
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class EquityPoint:
    timestamp: datetime
    equity: Decimal


@dataclass(frozen=True, slots=True)
class PaperFill:
    request_id: str
    symbol: str
    side: str
    quantity: Decimal
    fill_price: Decimal
    notional: Decimal
    realized_pnl: Decimal
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class PaperExecutionResult:
    status: ExecutionStatus
    reason: str
    original_proposal: ProposedOrder
    fill: PaperFill | None
    portfolio_before: PaperPortfolio
    portfolio_after: PaperPortfolio


@dataclass(frozen=True, slots=True)
class JournalEvent:
    sequence: int
    event_type: JournalEventType
    request_id: str
    timestamp: datetime
    message: str
    details: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class PaperJournal:
    events: tuple[JournalEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    win_rate: Decimal
    average_winner: Decimal | None
    average_loser: Decimal | None
    profit_factor: Decimal | None
    expectancy: Decimal | None
    total_return: Decimal
    maximum_drawdown: Decimal


@dataclass(frozen=True, slots=True)
class SimulationResult:
    execution: PaperExecutionResult
    portfolio: PaperPortfolio
    journal: PaperJournal
    equity_curve: tuple[EquityPoint, ...]
    metrics: PerformanceMetrics


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return json_safe(asdict(value))
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_safe(item) for item in value]
    return value
