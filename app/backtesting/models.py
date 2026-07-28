from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from app.compliance.models import AccountType, PurchaseLot
from app.order_compliance.kill_switch import KillSwitchState
from app.order_compliance.models import ComplianceLimits, MarketComplianceState, OrderSide, OrderType, TradingSession
from app.paper_trading.models import EquityPoint, PaperExecutionConfig, PaperJournal, PaperPortfolio
from app.risk.limits import DEFAULT_RISK_LIMITS, RiskLimits
from app.market_history import MarketObservation
if TYPE_CHECKING:
    from app.analytics import AnalyticsSnapshot


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


class PlaybackStatus(StrEnum):
    EMPTY = "EMPTY"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    CLOSED = "CLOSED"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class PlaybackSnapshot:
    status: PlaybackStatus
    position: int
    event_count: int
    speed: Decimal
    current_timestamp: datetime | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, PlaybackStatus):
            raise TypeError("status must be PlaybackStatus")
        for value, name in (
            (self.position, "position"),
            (self.event_count, "event_count"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be nonnegative")
        if self.position > self.event_count:
            raise ValueError("position cannot exceed event_count")
        _playback_decimal(self.speed, "speed", positive=True)
        _playback_timestamp(self.current_timestamp, "current_timestamp", True)
        _playback_optional_text(self.error, "error")

    @classmethod
    def initial(cls) -> "PlaybackSnapshot":
        return cls(PlaybackStatus.EMPTY, 0, 0, Decimal("1"))


@dataclass(frozen=True, slots=True)
class BacktestConfiguration:
    strategy_version: str
    speed: Decimal = Decimal("1")
    start_time: datetime | None = None
    end_time: datetime | None = None

    def __post_init__(self) -> None:
        _playback_text(self.strategy_version, "strategy_version")
        _playback_decimal(self.speed, "speed", positive=True)
        _playback_timestamp(self.start_time, "start_time", True)
        _playback_timestamp(self.end_time, "end_time", True)
        if (
            self.start_time is not None
            and self.end_time is not None
            and self.end_time < self.start_time
        ):
            raise ValueError("end_time cannot precede start_time")


@dataclass(frozen=True, slots=True)
class Experiment:
    experiment_id: str
    name: str
    configuration: BacktestConfiguration

    def __post_init__(self) -> None:
        _playback_text(self.experiment_id, "experiment_id")
        _playback_text(self.name, "name")
        if not isinstance(self.configuration, BacktestConfiguration):
            raise TypeError("configuration must be BacktestConfiguration")


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    experiment: Experiment
    playback_status: PlaybackStatus
    started_at: datetime
    ended_at: datetime
    processed_event_count: int
    recorded_session_id: str | None
    analytics: "AnalyticsSnapshot"
    completed_at: datetime
    error: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.experiment, Experiment):
            raise TypeError("experiment must be Experiment")
        if not isinstance(self.playback_status, PlaybackStatus):
            raise TypeError("playback_status must be PlaybackStatus")
        for value, name in (
            (self.started_at, "started_at"),
            (self.ended_at, "ended_at"),
            (self.completed_at, "completed_at"),
        ):
            _playback_timestamp(value, name)
        if self.ended_at < self.started_at:
            raise ValueError("ended_at cannot precede started_at")
        if (
            isinstance(self.processed_event_count, bool)
            or not isinstance(self.processed_event_count, int)
            or self.processed_event_count < 0
        ):
            raise ValueError("processed_event_count must be nonnegative")
        _playback_optional_text(
            self.recorded_session_id,
            "recorded_session_id",
        )
        from app.analytics import AnalyticsSnapshot
        if not isinstance(self.analytics, AnalyticsSnapshot):
            raise TypeError("analytics must be AnalyticsSnapshot")
        _playback_optional_text(self.error, "error")


@dataclass(frozen=True, slots=True)
class MetricComparison:
    name: str
    baseline: Decimal | int | timedelta | None
    candidate: Decimal | int | timedelta | None
    delta: Decimal | int | timedelta | None

    def __post_init__(self) -> None:
        _playback_text(self.name, "name")
        for value in (self.baseline, self.candidate, self.delta):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (Decimal, int, timedelta))
            ):
                raise TypeError("comparison values must be numeric, duration, or None")
            if isinstance(value, Decimal) and not value.is_finite():
                raise ValueError("comparison Decimal values must be finite")


@dataclass(frozen=True, slots=True)
class ComparisonSnapshot:
    baseline_experiment_id: str | None
    candidate_experiment_id: str | None
    metrics: tuple[MetricComparison, ...] = ()

    def __post_init__(self) -> None:
        _playback_optional_text(
            self.baseline_experiment_id,
            "baseline_experiment_id",
        )
        _playback_optional_text(
            self.candidate_experiment_id,
            "candidate_experiment_id",
        )
        if not isinstance(self.metrics, tuple) or any(
            not isinstance(value, MetricComparison)
            for value in self.metrics
        ):
            raise TypeError("metrics must be immutable MetricComparison values")

    @classmethod
    def initial(cls) -> "ComparisonSnapshot":
        return cls(None, None)


@dataclass(frozen=True, slots=True)
class ExperimentSnapshot:
    playback: PlaybackSnapshot
    experiments: tuple[ExperimentResult, ...]
    selected_experiment_id: str | None
    comparison: ComparisonSnapshot
    error: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.playback, PlaybackSnapshot):
            raise TypeError("playback must be PlaybackSnapshot")
        if not isinstance(self.experiments, tuple) or any(
            not isinstance(value, ExperimentResult)
            for value in self.experiments
        ):
            raise TypeError("experiments must be immutable ExperimentResult values")
        identifiers = tuple(
            value.experiment.experiment_id for value in self.experiments
        )
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("experiment identifiers must be unique")
        _playback_optional_text(
            self.selected_experiment_id,
            "selected_experiment_id",
        )
        if (
            self.selected_experiment_id is not None
            and self.selected_experiment_id not in identifiers
        ):
            raise ValueError("selected experiment must exist")
        if not isinstance(self.comparison, ComparisonSnapshot):
            raise TypeError("comparison must be ComparisonSnapshot")
        _playback_optional_text(self.error, "error")

    @classmethod
    def initial(cls) -> "ExperimentSnapshot":
        return cls(
            PlaybackSnapshot.initial(),
            (),
            None,
            ComparisonSnapshot.initial(),
        )


def _playback_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be stripped non-empty text")


def _playback_optional_text(value: str | None, name: str) -> None:
    if value is not None:
        _playback_text(value, name)


def _playback_timestamp(
    value: datetime | None,
    name: str,
    optional: bool = False,
) -> None:
    if optional and value is None:
        return
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")


def _playback_decimal(
    value: Decimal,
    name: str,
    *,
    positive: bool = False,
) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
