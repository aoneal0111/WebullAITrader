from __future__ import annotations

from decimal import Decimal

from app.analytics.models import (
    BacktestAnalyticsResult, ExperimentAnalyticsResult, ExperimentSuiteAnalyticsResult,
    WalkForwardAnalyticsResult,
)
from app.monte_carlo.bootstrap import DeterministicGenerator, bootstrap_sample, permutation_sample
from app.monte_carlo.models import (
    ExperimentMonteCarloResult, ExperimentSuiteMonteCarloResult, MonteCarloConfig,
    MonteCarloProbabilities, MonteCarloResult, SamplingMode, SimulationMetrics,
    WalkForwardMonteCarloItem, WalkForwardMonteCarloResult,
)
from app.monte_carlo.statistics import probability, summarize

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)


def run_monte_carlo(source: object, config: MonteCarloConfig) -> object:
    _validate_config(config)
    if isinstance(source, BacktestAnalyticsResult):
        return simulate_backtest(source, config)
    if isinstance(source, ExperimentAnalyticsResult):
        return ExperimentMonteCarloResult(source.experiment_id, source.configuration_fingerprint,
                                          simulate_backtest(source.backtest_analytics, config))
    if isinstance(source, ExperimentSuiteAnalyticsResult):
        return ExperimentSuiteMonteCarloResult(
            source.dataset_fingerprint,
            tuple(ExperimentMonteCarloResult(item.experiment_id, item.configuration_fingerprint,
                                             simulate_backtest(item.backtest_analytics, config))
                  for item in sorted(source.experiment_results, key=lambda item: item.experiment_id)),
        )
    if isinstance(source, WalkForwardAnalyticsResult):
        return WalkForwardMonteCarloResult(
            source.source_dataset_fingerprint,
            tuple(WalkForwardMonteCarloItem(item.window_index, item.experiment_id,
                                            simulate_backtest(item.analytics, config))
                  for item in sorted(source.window_results, key=lambda item: (item.window_index, item.experiment_id))),
        )
    raise ValueError("a completed analytics result is required")


def simulate_backtest(source: BacktestAnalyticsResult, config: MonteCarloConfig) -> MonteCarloResult:
    _validate_config(config)
    if not isinstance(source, BacktestAnalyticsResult):
        raise ValueError("BacktestAnalyticsResult is required")
    if config.use_trade_outcomes:
        values = tuple(item.realized_pnl for item in source.completed_trade_outcomes)
        source_kind = "TRADE_OUTCOMES"
    else:
        values = tuple(item.return_value for item in source.equity.return_observations)
        source_kind = "RETURN_SERIES"
    if not values:
        raise ValueError("selected sampling source has no observations")
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
        raise ValueError("sampling observations must be finite Decimals")
    generator = DeterministicGenerator(config.seed)
    runs = tuple(_simulate(index, _sample(values, generator, config.sampling_mode),
                           source.equity.starting_equity, config.use_trade_outcomes)
                 for index in range(config.simulation_count))
    profit_factors = tuple(item.profit_factor for item in runs if item.profit_factor is not None)
    win_rates = tuple(item.win_rate for item in runs if item.win_rate is not None)
    original_pf = source.trades.profit_factor
    return MonteCarloResult(
        source.source_identity, source_kind, len(values), config, runs,
        summarize(tuple(item.ending_equity for item in runs)),
        summarize(tuple(item.total_return for item in runs)),
        summarize(tuple(item.maximum_drawdown for item in runs)),
        summarize(profit_factors) if profit_factors else None,
        summarize(tuple(item.expectancy for item in runs)),
        summarize(win_rates) if win_rates else None,
        summarize(tuple(Decimal(item.maximum_consecutive_losses) for item in runs)),
        summarize(tuple(Decimal(item.maximum_consecutive_wins) for item in runs)),
        MonteCarloProbabilities(
            probability(tuple(item.total_return > ZERO for item in runs)),
            probability(tuple(item.total_return > source.equity.total_return for item in runs)),
            probability(tuple(item.maximum_drawdown > source.equity.maximum_drawdown for item in runs)),
            probability(tuple(item.profit_factor is not None and item.profit_factor > ONE for item in runs)),
            probability(tuple(item.expectancy > ZERO for item in runs)),
        ),
        (() if original_pf is not None else ("Original profit factor is undefined; it is not used as a probability benchmark.",)),
    )


def _simulate(index: int, values: tuple[Decimal, ...], starting: Decimal, trades: bool) -> SimulationMetrics:
    equity = starting
    peak = starting
    maximum_drawdown = ZERO
    wins = losses = directional = 0
    win_streak = loss_streak = max_wins = max_losses = 0
    gross_profit = gross_loss = ZERO
    for value in values:
        if trades:
            equity += value
        else:
            equity *= ONE + value
        peak = max(peak, equity)
        drawdown = ONE - equity / peak
        maximum_drawdown = max(maximum_drawdown, drawdown)
        if value > ZERO:
            wins += 1
            directional += 1
            gross_profit += value
            win_streak += 1
            loss_streak = 0
        elif value < ZERO:
            losses += 1
            directional += 1
            gross_loss += abs(value)
            loss_streak += 1
            win_streak = 0
        else:
            win_streak = loss_streak = 0
        max_wins, max_losses = max(max_wins, win_streak), max(max_losses, loss_streak)
    expectancy = sum(values, ZERO) / Decimal(len(values))
    return SimulationMetrics(index, equity, equity / starting - ONE, maximum_drawdown,
                             gross_profit / gross_loss if gross_loss else None, expectancy,
                             Decimal(wins) / Decimal(directional) * HUNDRED if directional else None,
                             max_losses, max_wins)


def _sample(values: tuple[Decimal, ...], generator: DeterministicGenerator,
            mode: SamplingMode) -> tuple[Decimal, ...]:
    if mode is SamplingMode.BOOTSTRAP:
        return bootstrap_sample(values, generator)
    if mode is SamplingMode.PERMUTATION:
        return permutation_sample(values, generator)
    raise ValueError("unsupported sampling mode")


def _validate_config(config: MonteCarloConfig) -> None:
    if not isinstance(config, MonteCarloConfig):
        raise ValueError("MonteCarloConfig is required")
    if not isinstance(config.seed, int) or isinstance(config.seed, bool):
        raise ValueError("a deterministic integer seed is required")
    if not isinstance(config.simulation_count, int) or isinstance(config.simulation_count, bool) or config.simulation_count <= 0:
        raise ValueError("simulation_count must be a positive integer")
    if not isinstance(config.sampling_mode, SamplingMode):
        raise ValueError("sampling_mode is invalid")
    if config.use_trade_outcomes == config.use_return_series:
        raise ValueError("exactly one sampling source must be selected")
