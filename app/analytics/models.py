from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from app.market_history import MarketObservation


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
