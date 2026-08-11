"""Immutable domain contracts for Warrior Momentum V1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from app.momentum_scanner.models import CatalystStatus, CatalystType

STRATEGY_ID = "WARRIOR_MOMENTUM_V1"


class CandidateStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    WATCH = "WATCH"
    NEAR_QUALIFIED = "NEAR_QUALIFIED"
    QUALIFIED = "QUALIFIED"
    SETUP_FORMING = "SETUP_FORMING"
    ENTRY_READY = "ENTRY_READY"
    INELIGIBLE_FOR_EXECUTION = "INELIGIBLE_FOR_EXECUTION"


class StockInPlayType(StrEnum):
    TOP_GAPPER = "TOP_GAPPER"
    HIGH_RELATIVE_VOLUME = "HIGH_RELATIVE_VOLUME"
    RUNNING_UP = "RUNNING_UP"
    HIGH_OF_DAY_MOMENTUM = "HIGH_OF_DAY_MOMENTUM"
    SQUEEZE_5_IN_5 = "SQUEEZE_5_IN_5"
    SQUEEZE_10_IN_10 = "SQUEEZE_10_IN_10"


class SetupType(StrEnum):
    HIGH_OF_DAY_BREAKOUT = "HIGH_OF_DAY_BREAKOUT"
    MICRO_PULLBACK = "MICRO_PULLBACK"
    BULL_FLAG = "BULL_FLAG"
    FLAT_TOP_BREAKOUT = "FLAT_TOP_BREAKOUT"


class SetupState(StrEnum):
    UNKNOWN = "UNKNOWN"
    NOT_FORMED = "NOT_FORMED"
    FORMING = "FORMING"
    TRIGGERED = "TRIGGERED"


class StopModel(StrEnum):
    MICRO_PULLBACK_LOW = "MICRO_PULLBACK_LOW"
    FLAG_LOW = "FLAG_LOW"
    BREAKOUT_LEVEL = "BREAKOUT_LEVEL"
    RECENT_SWING_LOW = "RECENT_SWING_LOW"
    MAX_RISK_DISTANCE = "MAX_RISK_DISTANCE"


class ReasonCode(StrEnum):
    PRICE_TOO_LOW = "PRICE_TOO_LOW"
    PRICE_TOO_HIGH = "PRICE_TOO_HIGH"
    CHANGE_TOO_LOW = "CHANGE_TOO_LOW"
    RVOL_LOW = "RVOL_LOW"
    FLOAT_HIGH = "FLOAT_HIGH"
    LIQUIDITY_LOW = "LIQUIDITY_LOW"
    SPREAD_WIDE = "SPREAD_WIDE"
    NO_CATALYST = "NO_CATALYST"
    CATALYST_UNKNOWN = "CATALYST_UNKNOWN"
    NO_SETUP = "NO_SETUP"
    BREAKOUT_NOT_CONFIRMED = "BREAKOUT_NOT_CONFIRMED"
    STOP_TOO_WIDE = "STOP_TOO_WIDE"
    STOP_INVALID = "STOP_INVALID"
    HALTED = "HALTED"
    HALT_UNKNOWN = "HALT_UNKNOWN"
    NOT_TRADABLE = "NOT_TRADABLE"
    SESSION_NOT_ALLOWED = "SESSION_NOT_ALLOWED"
    EXECUTION_NOT_ALLOWED = "EXECUTION_NOT_ALLOWED"
    RISK_REJECTED = "RISK_REJECTED"


@dataclass(frozen=True, slots=True)
class MinuteBar:
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("bar timestamp must be timezone-aware")
        if not self.symbol.strip() or self.volume < 0 or min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("bar fields are invalid")
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close, self.high):
            raise ValueError("bar OHLC is inconsistent")


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    symbol: str
    timestamp: datetime
    session_high: Decimal
    session_low: Decimal
    rolling_high: Decimal
    rolling_low: Decimal
    rolling_change_percent: Decimal
    rolling_volume: Decimal
    volume_acceleration: Decimal | None
    vwap: Decimal | None
    distance_from_vwap_percent: Decimal | None
    distance_from_hod_percent: Decimal
    pullback_depth_percent: Decimal
    consolidation_duration: int
    breakout_level: Decimal | None
    breakout_volume_ratio: Decimal | None


@dataclass(frozen=True, slots=True)
class MomentumScore:
    total: Decimal
    components: tuple[tuple[str, Decimal], ...]


@dataclass(frozen=True, slots=True)
class SetupDetection:
    setup_type: SetupType
    state: SetupState
    score: Decimal
    trigger: Decimal | None = None
    stop_price: Decimal | None = None
    stop_model: StopModel | None = None
    resistance: Decimal | None = None
    reason_codes: tuple[ReasonCode, ...] = ()


@dataclass(frozen=True, slots=True)
class MomentumCandidate:
    rank: int
    symbol: str
    timestamp: datetime
    price: Decimal
    percentage_change: Decimal
    relative_volume: Decimal
    float_shares: Decimal | None
    volume: Decimal
    dollar_volume: Decimal
    spread_percent: Decimal | None
    catalyst_status: CatalystStatus
    catalyst_type: CatalystType
    score: MomentumScore
    stocks_in_play: tuple[StockInPlayType, ...]
    setup: SetupDetection | None
    session: str
    status: CandidateStatus
    tradable: bool
    halted: bool
    distance_from_hod_percent: Decimal | None
    reason_codes: tuple[ReasonCode, ...]
    explanations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MomentumEntrySignal:
    strategy_id: str
    symbol: str
    timestamp: datetime
    session: str
    momentum_score: Decimal
    setup_type: SetupType
    entry_trigger: Decimal
    reference_price: Decimal
    stop_price: Decimal
    stop_model: StopModel
    risk_per_share: Decimal
    target_levels: tuple[Decimal, ...]
    catalyst_state: CatalystStatus
    relative_volume: Decimal
    float_shares: Decimal | None
    spread_percent: Decimal | None
    volume: Decimal
    dollar_volume: Decimal
    setup_score: Decimal
    reasoning_codes: tuple[ReasonCode, ...]
    execution_authorized: bool = False

    def __post_init__(self) -> None:
        if self.strategy_id != STRATEGY_ID:
            raise ValueError("unexpected strategy id")
        if self.risk_per_share <= 0 or self.entry_trigger <= self.stop_price:
            raise ValueError("signal requires a valid structural stop")
        if self.execution_authorized:
            raise ValueError("Warrior Momentum V1 cannot authorize execution")


@dataclass(frozen=True, slots=True)
class PositionSize:
    shares: int
    risk_dollars: Decimal
    position_dollars: Decimal
    approved: bool
    reason_codes: tuple[ReasonCode, ...] = ()


@dataclass(frozen=True, slots=True)
class PaperExit:
    label: str
    price: Decimal
    quantity: int


@dataclass(frozen=True, slots=True)
class PaperTradePlan:
    signal: MomentumEntrySignal
    position: PositionSize
    exits: tuple[PaperExit, ...]
    paper_execution_authorized: bool
    live_execution_authorized: bool = False

    def __post_init__(self) -> None:
        if self.live_execution_authorized:
            raise ValueError("Warrior Momentum V1 cannot authorize live execution")
        if self.paper_execution_authorized != self.position.approved:
            raise ValueError("paper authorization must match the approved position")


@dataclass(frozen=True, slots=True)
class HaltObservation:
    symbol: str
    entered_at: datetime
    resumed_at: datetime | None = None
    duration: timedelta | None = None
    resume_gap_percent: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ReplayTrade:
    symbol: str
    setup_type: SetupType
    catalyst_state: CatalystStatus
    session: str
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    initial_risk: Decimal
    quantity: int
    r_multiple: Decimal
    pnl: Decimal
    mae_r: Decimal
    mfe_r: Decimal
    float_shares: Decimal | None
    relative_volume: Decimal
    momentum_score: Decimal


@dataclass(frozen=True, slots=True)
class ReplayReport:
    discovered_stocks: int
    setups: int
    signals: int
    wins: int
    losses: int
    win_rate: Decimal
    loss_rate: Decimal
    profit_factor: Decimal | None
    expectancy: Decimal
    average_r: Decimal
    median_r: Decimal
    max_drawdown: Decimal
    average_hold_seconds: Decimal
    best_trade: ReplayTrade | None
    worst_trade: ReplayTrade | None
    average_mae_r: Decimal
    average_mfe_r: Decimal
    slippage_sensitivity: tuple[tuple[Decimal, Decimal], ...]
    spread_sensitivity: tuple[tuple[Decimal, Decimal], ...]
    breakdowns: tuple[tuple[str, tuple[tuple[str, Decimal], ...]], ...]


__all__ = [name for name in globals() if not name.startswith("_")]
