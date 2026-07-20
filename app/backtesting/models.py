from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Any

from app.compliance.models import AccountType, PurchaseLot
from app.order_compliance.kill_switch import KillSwitchState
from app.order_compliance.models import ComplianceLimits, MarketComplianceState, OrderSide, OrderType, TradingSession
from app.paper_trading.models import EquityPoint, PaperExecutionConfig, PaperJournal, PaperPortfolio
from app.risk.limits import DEFAULT_RISK_LIMITS, RiskLimits
from app.market_history import MarketObservation


@dataclass(frozen=True, slots=True)
class HistoricalCandle:
    symbol: str
    open_timestamp: datetime
    close_timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True, slots=True)
class HistoricalFrame:
    candle: HistoricalCandle
    market_state: MarketComplianceState
    execution_bid: Decimal
    execution_ask: Decimal
    execution_last: Decimal
    session: TradingSession | None = None
    observed_slippage: Decimal | None = None
    volatility_regime: str | None = None
    trend_regime: str | None = None


@dataclass(frozen=True, slots=True)
class SuppliedAIResponse:
    candle_timestamp: datetime
    symbol: str
    raw_json: str


@dataclass(frozen=True, slots=True)
class BacktestOrderIntent:
    candle_timestamp: datetime
    request_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    order_type: OrderType
    limit_price: Decimal | None
    stop_price: Decimal | None
    requested_session: TradingSession


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    account_type: AccountType
    initial_cash: Decimal
    compliance_limits: ComplianceLimits
    paper_execution_config: PaperExecutionConfig
    kill_switch: KillSwitchState
    settlement_holidays: frozenset[str] = frozenset()
    warmup_candles: int = 26
    strategy_version: str = "1.0"
    prompt_version: str = "1.0"
    checkpoint_schema_version: int = 3
    risk_limits: RiskLimits = DEFAULT_RISK_LIMITS


class ReplayEventType(StrEnum):
    CANDLE = "CANDLE"
    INDICATORS = "INDICATORS"
    STRATEGY = "STRATEGY"
    PROMPT = "PROMPT"
    AI_RESPONSE = "AI_RESPONSE"
    AI_REJECTION = "AI_REJECTION"
    RISK = "RISK"
    GFV = "GFV"
    ORDER_COMPLIANCE = "ORDER_COMPLIANCE"
    PAPER_EXECUTION = "PAPER_EXECUTION"
    PORTFOLIO = "PORTFOLIO"
    CHECKPOINT = "CHECKPOINT"


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    sequence: int
    candle_index: int
    timestamp: datetime
    event_type: ReplayEventType
    status: str
    details: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ReplayJournal:
    events: tuple[ReplayEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class PendingExecution:
    proposal_json: str
    compliance_json: str


@dataclass(frozen=True, slots=True)
class ReplayCheckpoint:
    schema_version: int
    dataset_fingerprint: str
    response_fingerprint: str
    intent_fingerprint: str
    config_fingerprint: str
    next_candle_index: int
    portfolio: PaperPortfolio
    paper_journal: PaperJournal
    replay_journal: ReplayJournal
    equity_curve: tuple[EquityPoint, ...]
    portfolio_history: tuple[PaperPortfolio, ...]
    purchase_lots: tuple[PurchaseLot, ...]
    pending_execution: PendingExecution | None
    proposals: int
    approved: int
    rejected: int
    filled: int
    market_observations: tuple[MarketObservation, ...] = ()

    def to_json(self) -> str:
        return json.dumps(_json_safe(asdict(self)), sort_keys=True, separators=(",", ":"))


def canonical_fingerprint(value: Any) -> str:
    payload = json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, frozenset):
        return sorted(value)
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return value
