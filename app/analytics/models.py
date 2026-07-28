from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from app.market_history import MarketObservation
from app.analytics.domain_models import AnalyticsStatus


@dataclass(frozen=True, slots=True)
class AnalyticsConfig:
    annualization_periods: int | None = None
    risk_free_rate: Decimal = Decimal("0")
    minimum_acceptable_return: Decimal = Decimal("0")
    return_interval: str = "equity_observation"
    rolling_window: int | None = None


@dataclass(frozen=True, slots=True)
class ReturnObservation:
    timestamp: datetime
    return_value: Decimal


@dataclass(frozen=True, slots=True)
class DrawdownObservation:
    timestamp: datetime
    equity: Decimal
    running_peak: Decimal
    drawdown: Decimal


@dataclass(frozen=True, slots=True)
class DrawdownEpisode:
    peak_timestamp: datetime
    trough_timestamp: datetime
    recovery_timestamp: datetime | None
    peak_equity: Decimal
    trough_equity: Decimal
    drawdown: Decimal
    decline_duration_microseconds: int
    recovery_duration_microseconds: int | None
    total_underwater_duration_microseconds: int | None


@dataclass(frozen=True, slots=True)
class TradeOutcome:
    close_timestamp: datetime
    realized_pnl: Decimal
    is_win: bool
    is_loss: bool
    is_breakeven: bool
    holding_duration_microseconds: int | None
    capital_at_entry: Decimal | None
    symbol: str | None
    request_id: str
    journal_sequence: int


@dataclass(frozen=True, slots=True)
class EquityAnalytics:
    starting_equity: Decimal
    ending_equity: Decimal
    total_return: Decimal
    number_of_equity_observations: int
    return_observations: tuple[ReturnObservation, ...]
    drawdown_episodes: tuple[DrawdownEpisode, ...]
    maximum_drawdown: Decimal
    average_drawdown: Decimal
    longest_underwater_duration_microseconds: int
    underwater_duration_percent: Decimal | None
    current_drawdown: Decimal
    recovered_episode_count: int
    unrecovered_episode_count: int


@dataclass(frozen=True, slots=True)
class RiskAnalytics:
    arithmetic_mean_return: Decimal | None
    return_standard_deviation: Decimal | None
    downside_deviation: Decimal | None
    period_sharpe_ratio: Decimal | None
    annualized_sharpe_ratio: Decimal | None
    period_sortino_ratio: Decimal | None
    annualized_sortino_ratio: Decimal | None
    annualized_return: Decimal | None
    annualized_volatility: Decimal | None
    calmar_ratio: Decimal | None
    return_interval: str
    number_of_returns: int


@dataclass(frozen=True, slots=True)
class ExposureAnalytics:
    available: bool
    observed_duration_microseconds: int | None
    invested_duration_microseconds: int | None
    time_in_market_percent: Decimal | None
    average_gross_exposure_percent: Decimal | None
    maximum_gross_exposure_percent: Decimal | None
    average_net_exposure_percent: Decimal | None
    maximum_absolute_net_exposure_percent: Decimal | None
    average_capital_utilization_percent: Decimal | None
    maximum_capital_utilization_percent: Decimal | None
    average_holding_duration_microseconds: Decimal | None
    median_holding_duration_microseconds: Decimal | None
    maximum_holding_duration_microseconds: int | None
    completed_holding_count: int | None
    prerequisites: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TradeAnalytics:
    total_completed_outcomes: int
    winning_outcomes: int
    losing_outcomes: int
    breakeven_outcomes: int
    win_rate: Decimal | None
    loss_rate: Decimal | None
    average_winner: Decimal | None
    average_loser: Decimal | None
    largest_winner: Decimal | None
    largest_loser: Decimal | None
    median_outcome: Decimal | None
    gross_profit: Decimal
    gross_loss: Decimal
    profit_factor: Decimal | None
    expectancy: Decimal | None
    payoff_ratio: Decimal | None
    maximum_consecutive_wins: int
    maximum_consecutive_losses: int


@dataclass(frozen=True, slots=True)
class DistributionAnalytics:
    count: int
    minimum: Decimal | None
    maximum: Decimal | None
    mean: Decimal | None
    median: Decimal | None
    population_standard_deviation: Decimal | None
    percentile_01: Decimal | None
    percentile_05: Decimal | None
    percentile_10: Decimal | None
    percentile_25: Decimal | None
    percentile_50: Decimal | None
    percentile_75: Decimal | None
    percentile_90: Decimal | None
    percentile_95: Decimal | None
    percentile_99: Decimal | None
    skewness: Decimal | None
    excess_kurtosis: Decimal | None


@dataclass(frozen=True, slots=True)
class RealizedPnlGroup:
    key: str
    observation_count: int
    total_realized_pnl: Decimal
    mean_realized_pnl: Decimal
    win_rate: Decimal


@dataclass(frozen=True, slots=True)
class RollingObservation:
    ending_timestamp: datetime
    observation_count: int
    value: Decimal | None


@dataclass(frozen=True, slots=True)
class BacktestAnalyticsResult:
    source_identity: str
    dataset_fingerprint: str
    config_fingerprint: str
    response_fingerprint: str
    intent_fingerprint: str
    equity: EquityAnalytics
    risk: RiskAnalytics
    exposure: ExposureAnalytics
    trades: TradeAnalytics
    return_distribution: DistributionAnalytics
    daily_return_distribution: DistributionAnalytics
    weekly_return_distribution: DistributionAnalytics
    monthly_return_distribution: DistributionAnalytics
    pnl_by_month: tuple[RealizedPnlGroup, ...]
    pnl_by_weekday: tuple[RealizedPnlGroup, ...]
    pnl_by_hour: tuple[RealizedPnlGroup, ...]
    rolling_win_rate: tuple[RollingObservation, ...]
    rolling_expectancy: tuple[RollingObservation, ...]
    rolling_mean_return: tuple[RollingObservation, ...]
    rolling_volatility: tuple[RollingObservation, ...]
    warnings: tuple[str, ...]
    metric_definition_version: str = "1.0"
    completed_trade_outcomes: tuple[TradeOutcome, ...] = ()
    market_observations: tuple[MarketObservation, ...] = ()


@dataclass(frozen=True, slots=True)
class ExperimentAnalyticsResult:
    experiment_id: str
    configuration_fingerprint: str
    backtest_analytics: BacktestAnalyticsResult


@dataclass(frozen=True, slots=True)
class ExperimentSuiteAnalyticsResult:
    dataset_fingerprint: str
    experiment_results: tuple[ExperimentAnalyticsResult, ...]


@dataclass(frozen=True, slots=True)
class WalkForwardWindowExperimentAnalytics:
    window_index: int
    experiment_id: str
    analytics: BacktestAnalyticsResult


@dataclass(frozen=True, slots=True)
class WalkForwardExperimentAggregateAnalytics:
    experiment_id: str
    compounded_return: Decimal
    maximum_window_drawdown: Decimal
    trades: TradeAnalytics
    return_distribution: DistributionAnalytics
    continuous_equity_risk_available: bool = False


@dataclass(frozen=True, slots=True)
class WalkForwardAnalyticsResult:
    source_dataset_fingerprint: str
    window_results: tuple[WalkForwardWindowExperimentAnalytics, ...]
    experiment_aggregates: tuple[WalkForwardExperimentAggregateAnalytics, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: Decimal = Decimal("0")
    average_gain: Decimal = Decimal("0")
    average_loss: Decimal = Decimal("0")
    profit_factor: Decimal | None = None
    expectancy: Decimal = Decimal("0")
    average_holding_duration: timedelta | None = None
    average_trade_duration: timedelta | None = None
    largest_winner: Decimal = Decimal("0")
    largest_loser: Decimal = Decimal("0")
    net_realized_pnl: Decimal = Decimal("0")
    gross_profit: Decimal = Decimal("0")
    gross_loss: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        for name in ("total_trades", "winning_trades", "losing_trades"):
            _historical_count(getattr(self, name), name)
        if self.winning_trades + self.losing_trades > self.total_trades:
            raise ValueError("winning and losing trades exceed total trades")
        for name in (
            "win_rate", "average_gain", "average_loss", "expectancy",
            "largest_winner", "largest_loser", "net_realized_pnl",
            "gross_profit", "gross_loss",
        ):
            _historical_decimal(getattr(self, name), name)
        if not Decimal("0") <= self.win_rate <= Decimal("1"):
            raise ValueError("win_rate must be between zero and one")
        if self.average_gain < 0 or self.gross_profit < 0:
            raise ValueError("gain metrics must be nonnegative")
        if self.average_loss > 0 or self.gross_loss > 0:
            raise ValueError("loss metrics must be nonpositive")
        if self.profit_factor is not None:
            _historical_decimal(self.profit_factor, "profit_factor")
            if self.profit_factor < 0:
                raise ValueError("profit_factor must be nonnegative")
        for name in ("average_holding_duration", "average_trade_duration"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, timedelta)
                or value < timedelta()
            ):
                raise ValueError(f"{name} must be a nonnegative timedelta")


@dataclass(frozen=True, slots=True)
class RiskMetrics:
    maximum_drawdown: Decimal = Decimal("0")
    rolling_drawdown: tuple[Decimal, ...] = ()
    peak_equity: Decimal = Decimal("0")
    recovery_factor: Decimal | None = None
    ulcer_index: Decimal = Decimal("0")
    average_exposure: Decimal = Decimal("0")
    largest_position: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        for name in (
            "maximum_drawdown", "peak_equity", "ulcer_index",
            "average_exposure", "largest_position",
        ):
            _historical_decimal(getattr(self, name), name)
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be nonnegative")
        if not isinstance(self.rolling_drawdown, tuple):
            raise TypeError("rolling_drawdown must be an immutable tuple")
        for value in self.rolling_drawdown:
            _historical_decimal(value, "rolling_drawdown")
            if value < 0:
                raise ValueError("rolling_drawdown must be nonnegative")
        if self.recovery_factor is not None:
            _historical_decimal(self.recovery_factor, "recovery_factor")


@dataclass(frozen=True, slots=True)
class StrategyMetrics:
    by_strategy_version: tuple[tuple[str, int, Decimal], ...] = ()
    by_decision: tuple[tuple[str, int, Decimal], ...] = ()
    by_lifecycle_phase: tuple[tuple[str, int], ...] = ()
    by_committee_outcome: tuple[tuple[str, int, Decimal], ...] = ()

    def __post_init__(self) -> None:
        _historical_groups(self.by_strategy_version, "by_strategy_version", True)
        _historical_groups(self.by_decision, "by_decision", True)
        _historical_groups(self.by_lifecycle_phase, "by_lifecycle_phase", False)
        _historical_groups(self.by_committee_outcome, "by_committee_outcome", True)


@dataclass(frozen=True, slots=True)
class SymbolMetrics:
    symbol: str
    performance: PerformanceMetrics

    def __post_init__(self) -> None:
        _historical_text(self.symbol, "symbol")
        if self.symbol != self.symbol.upper():
            raise ValueError("symbol must be uppercase")
        if not isinstance(self.performance, PerformanceMetrics):
            raise TypeError("performance must be PerformanceMetrics")


@dataclass(frozen=True, slots=True)
class TimeMetrics:
    dimension: str
    period: str
    total_trades: int
    winning_trades: int
    realized_pnl: Decimal

    def __post_init__(self) -> None:
        _historical_text(self.dimension, "dimension")
        _historical_text(self.period, "period")
        _historical_count(self.total_trades, "total_trades")
        _historical_count(self.winning_trades, "winning_trades")
        if self.winning_trades > self.total_trades:
            raise ValueError("winning_trades cannot exceed total_trades")
        _historical_decimal(self.realized_pnl, "realized_pnl")


@dataclass(frozen=True, slots=True)
class AnalyticsSnapshot:
    status: AnalyticsStatus
    performance: PerformanceMetrics
    risk: RiskMetrics
    strategy: StrategyMetrics
    symbols: tuple[SymbolMetrics, ...]
    time_metrics: tuple[TimeMetrics, ...]
    selected_symbol: str | None
    selected_strategy: str | None
    updated_at: datetime | None
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, AnalyticsStatus):
            raise TypeError("status must be AnalyticsStatus")
        if not isinstance(self.performance, PerformanceMetrics):
            raise TypeError("performance must be PerformanceMetrics")
        if not isinstance(self.risk, RiskMetrics):
            raise TypeError("risk must be RiskMetrics")
        if not isinstance(self.strategy, StrategyMetrics):
            raise TypeError("strategy must be StrategyMetrics")
        _historical_instances(self.symbols, SymbolMetrics, "symbols")
        _historical_instances(self.time_metrics, TimeMetrics, "time_metrics")
        for value, name in (
            (self.selected_symbol, "selected_symbol"),
            (self.selected_strategy, "selected_strategy"),
        ):
            if value is not None:
                _historical_text(value, name)
        if self.selected_symbol is not None and (
            self.selected_symbol != self.selected_symbol.upper()
        ):
            raise ValueError("selected_symbol must be uppercase")
        if self.updated_at is not None and (
            not isinstance(self.updated_at, datetime)
            or self.updated_at.tzinfo is None
        ):
            raise ValueError("updated_at must be timezone-aware")
        if not isinstance(self.errors, tuple) or any(
            not isinstance(value, str) or not value.strip()
            for value in self.errors
        ):
            raise ValueError("errors must be immutable non-empty strings")

    @classmethod
    def initial(cls) -> "AnalyticsSnapshot":
        return cls(
            AnalyticsStatus.EMPTY,
            PerformanceMetrics(),
            RiskMetrics(),
            StrategyMetrics(),
            (),
            (),
            None,
            None,
            None,
            (),
        )


def _historical_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be stripped non-empty text")


def _historical_decimal(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")


def _historical_count(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


def _historical_instances(value: tuple, kind: type, name: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be an immutable tuple")
    if any(not isinstance(item, kind) for item in value):
        raise TypeError(f"{name} contains an invalid item")


def _historical_groups(value: tuple, name: str, includes_pnl: bool) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be an immutable tuple")
    expected = 3 if includes_pnl else 2
    for item in value:
        if not isinstance(item, tuple) or len(item) != expected:
            raise TypeError(f"{name} contains an invalid group")
        _historical_text(item[0], f"{name} key")
        _historical_count(item[1], f"{name} count")
        if includes_pnl:
            _historical_decimal(item[2], f"{name} pnl")
