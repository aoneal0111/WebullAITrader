from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class ScenarioKind(StrEnum):
    MARKET_CRASH = "MARKET_CRASH"
    BEAR_MARKET = "BEAR_MARKET"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    GAP_HEAVY = "GAP_HEAVY"
    TRENDING_MARKET = "TRENDING_MARKET"
    SIDEWAYS_MARKET = "SIDEWAYS_MARKET"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    HIGH_SPREAD = "HIGH_SPREAD"
    HIGH_SLIPPAGE = "HIGH_SLIPPAGE"
    TRADING_HALTS = "TRADING_HALTS"
    CUSTOM = "CUSTOM"


@dataclass(frozen=True, slots=True)
class ComparisonThreshold:
    metric: str
    maximum_adverse_difference: Decimal


@dataclass(frozen=True, slots=True)
class ScenarioFilter:
    scenario: ScenarioKind
    start_timestamp: datetime | None = None
    end_timestamp: datetime | None = None
    volatility_regime: str | None = None
    trend_regime: str | None = None
    session: str | None = None
    symbol: str | None = None
    weekdays: tuple[int, ...] = ()
    months: tuple[int, ...] = ()
    hours: tuple[int, ...] = ()
    minimum_drawdown: Decimal | None = None
    maximum_drawdown: Decimal | None = None
    maximum_volume: Decimal | None = None
    minimum_gap_percent: Decimal | None = None
    minimum_spread: Decimal | None = None
    minimum_absolute_slippage: Decimal | None = None
    outcome: str | None = None
    thresholds: tuple[ComparisonThreshold, ...] = ()


@dataclass(frozen=True, slots=True)
class StressTestConfig:
    enabled_scenarios: tuple[ScenarioKind, ...]
    custom_filters: tuple[ScenarioFilter, ...]
    comparison_tolerance: Decimal
    annualization_periods: int | None
    include_exposure_metrics: bool


@dataclass(frozen=True, slots=True)
class ScenarioMetrics:
    total_return: Decimal
    cagr: Decimal | None
    maximum_drawdown: Decimal
    win_rate: Decimal | None
    loss_rate: Decimal | None
    profit_factor: Decimal | None
    expectancy: Decimal | None
    sharpe: Decimal | None
    sortino: Decimal | None
    calmar: Decimal | None
    number_of_trades: int
    average_gross_exposure_percent: Decimal | None
    time_in_market_percent: Decimal | None
    maximum_consecutive_wins: int
    maximum_consecutive_losses: int


@dataclass(frozen=True, slots=True)
class MetricComparison:
    metric: str
    original: Decimal | None
    scenario: Decimal | None
    absolute_difference: Decimal | None
    percentage_difference: Decimal | None
    label: str
    passed: bool | None


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    scenario: ScenarioKind
    available: bool
    observation_count: int
    metrics: ScenarioMetrics | None
    comparisons: tuple[MetricComparison, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StressTestResult:
    source_identity: str
    scenarios: tuple[ScenarioResult, ...]


@dataclass(frozen=True, slots=True)
class ExperimentStressTestResult:
    experiment_id: str
    configuration_fingerprint: str
    result: StressTestResult


@dataclass(frozen=True, slots=True)
class ExperimentSuiteStressTestResult:
    dataset_fingerprint: str
    experiment_results: tuple[ExperimentStressTestResult, ...]


@dataclass(frozen=True, slots=True)
class WalkForwardStressTestItem:
    window_index: int
    experiment_id: str
    result: StressTestResult


@dataclass(frozen=True, slots=True)
class WalkForwardStressTestResult:
    source_dataset_fingerprint: str
    window_results: tuple[WalkForwardStressTestItem, ...]
