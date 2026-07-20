from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class SamplingMode(StrEnum):
    BOOTSTRAP = "BOOTSTRAP"
    PERMUTATION = "PERMUTATION"


@dataclass(frozen=True, slots=True)
class MonteCarloConfig:
    seed: int
    simulation_count: int
    sampling_mode: SamplingMode
    use_trade_outcomes: bool
    use_return_series: bool


@dataclass(frozen=True, slots=True)
class SimulationMetrics:
    simulation_index: int
    ending_equity: Decimal
    total_return: Decimal
    maximum_drawdown: Decimal
    profit_factor: Decimal | None
    expectancy: Decimal
    win_rate: Decimal | None
    maximum_consecutive_losses: int
    maximum_consecutive_wins: int


@dataclass(frozen=True, slots=True)
class MetricSummary:
    mean: Decimal
    median: Decimal
    minimum: Decimal
    maximum: Decimal
    population_standard_deviation: Decimal
    percentile_05: Decimal
    percentile_25: Decimal
    percentile_50: Decimal
    percentile_75: Decimal
    percentile_95: Decimal


@dataclass(frozen=True, slots=True)
class MonteCarloProbabilities:
    finishing_positive: Decimal
    exceeding_original_return: Decimal
    drawdown_exceeding_original: Decimal
    profit_factor_above_one: Decimal
    expectancy_positive: Decimal


@dataclass(frozen=True, slots=True)
class MonteCarloResult:
    source_identity: str
    source_kind: str
    source_observation_count: int
    config: MonteCarloConfig
    simulations: tuple[SimulationMetrics, ...]
    ending_equity: MetricSummary
    total_return: MetricSummary
    maximum_drawdown: MetricSummary
    profit_factor: MetricSummary | None
    expectancy: MetricSummary
    win_rate: MetricSummary | None
    maximum_consecutive_losses: MetricSummary
    maximum_consecutive_wins: MetricSummary
    probabilities: MonteCarloProbabilities
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExperimentMonteCarloResult:
    experiment_id: str
    configuration_fingerprint: str
    result: MonteCarloResult


@dataclass(frozen=True, slots=True)
class ExperimentSuiteMonteCarloResult:
    dataset_fingerprint: str
    experiment_results: tuple[ExperimentMonteCarloResult, ...]


@dataclass(frozen=True, slots=True)
class WalkForwardMonteCarloItem:
    window_index: int
    experiment_id: str
    result: MonteCarloResult


@dataclass(frozen=True, slots=True)
class WalkForwardMonteCarloResult:
    source_dataset_fingerprint: str
    window_results: tuple[WalkForwardMonteCarloItem, ...]
