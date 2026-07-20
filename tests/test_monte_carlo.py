from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.analytics.distribution import analyze_distribution
from app.analytics.equity import analyze_equity
from app.analytics.models import (
    BacktestAnalyticsResult, ExperimentAnalyticsResult, ExperimentSuiteAnalyticsResult,
    ExposureAnalytics, RiskAnalytics, TradeOutcome, WalkForwardAnalyticsResult,
    WalkForwardWindowExperimentAnalytics,
)
from app.analytics.performance import analyze_trades
from app.monte_carlo import (
    ExperimentSuiteMonteCarloResult, MonteCarloConfig, SamplingMode,
    WalkForwardMonteCarloResult, monte_carlo_to_json, monte_carlo_to_text, run_monte_carlo,
)
from app.monte_carlo.bootstrap import DeterministicGenerator, bootstrap_sample, permutation_sample
from app.paper_trading.models import EquityPoint

D = Decimal
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def source(outcomes: tuple[str, ...] = ("10", "-5", "3", "-2")) -> BacktestAnalyticsResult:
    curve = tuple(EquityPoint(T0 + timedelta(days=index), value)
                  for index, value in enumerate((D("100"), D("110"), D("105"), D("115"))))
    equity = analyze_equity(curve)
    trades = tuple(TradeOutcome(T0 + timedelta(hours=index), D(value), D(value) > 0, D(value) < 0,
                                D(value) == 0, None, None, "XYZ", str(index), index)
                   for index, value in enumerate(outcomes))
    unavailable = ExposureAnalytics(False, None, None, None, None, None, None, None, None, None,
                                    None, None, None, None, ("unavailable",))
    risk = RiskAnalytics(None, None, None, None, None, None, None, None, None, None,
                         "equity_observation", len(equity.return_observations))
    empty = analyze_distribution(())
    return BacktestAnalyticsResult(
        source_identity="source", dataset_fingerprint="dataset", config_fingerprint="config",
        response_fingerprint="responses", intent_fingerprint="intents", equity=equity, risk=risk,
        exposure=unavailable, trades=analyze_trades(trades),
        return_distribution=analyze_distribution(tuple(item.return_value for item in equity.return_observations)),
        daily_return_distribution=empty, weekly_return_distribution=empty, monthly_return_distribution=empty,
        pnl_by_month=(), pnl_by_weekday=(), pnl_by_hour=(), rolling_win_rate=(), rolling_expectancy=(),
        rolling_mean_return=(), rolling_volatility=(), warnings=(), completed_trade_outcomes=trades,
    )


def config(seed: int = 7, *, trades: bool = True,
           mode: SamplingMode = SamplingMode.BOOTSTRAP) -> MonteCarloConfig:
    return MonteCarloConfig(seed, 20, mode, trades, not trades)


def test_same_seed_is_identical_and_different_seed_changes_sequence() -> None:
    first = run_monte_carlo(source(), config())
    second = run_monte_carlo(source(), config())
    different = run_monte_carlo(source(), config(8))
    assert first == second
    assert first.simulations != different.simulations


def test_bootstrap_and_permutation_are_deterministic_and_correct() -> None:
    values = (D("1"), D("2"), D("3"), D("4"))
    first = bootstrap_sample(values, DeterministicGenerator(4))
    assert first == bootstrap_sample(values, DeterministicGenerator(4))
    assert len(first) == len(values) and set(first) <= set(values)
    permutation = permutation_sample(values, DeterministicGenerator(4))
    assert sorted(permutation) == sorted(values)
    assert permutation == permutation_sample(values, DeterministicGenerator(4))


def test_permutation_preserves_aggregate_trade_metrics_but_changes_paths() -> None:
    result = run_monte_carlo(source(), config(mode=SamplingMode.PERMUTATION))
    assert all(item.expectancy == D("1.5") for item in result.simulations)
    assert all(item.profit_factor == D("13") / D("7") for item in result.simulations)
    assert len({item.maximum_drawdown for item in result.simulations}) > 1


def test_return_series_compounds_exact_decimals() -> None:
    result = run_monte_carlo(source(), MonteCarloConfig(1, 1, SamplingMode.PERMUTATION, False, True))
    assert result.simulations[0].ending_equity == D("115")
    assert result.simulations[0].total_return == D("0.15")
    assert not any(isinstance(value, float) for value in result.simulations[0].__dict__.values()) if hasattr(result.simulations[0], "__dict__") else True


def test_one_trade_and_empty_source_validation() -> None:
    result = run_monte_carlo(source(("5",)), MonteCarloConfig(1, 2, SamplingMode.BOOTSTRAP, True, False))
    assert all(item.ending_equity == D("105") for item in result.simulations)
    with pytest.raises(ValueError, match="no observations"):
        run_monte_carlo(source(()), config())


@pytest.mark.parametrize("bad", [
    MonteCarloConfig(1, 0, SamplingMode.BOOTSTRAP, True, False),
    MonteCarloConfig(1, 1, SamplingMode.BOOTSTRAP, True, True),
    MonteCarloConfig(1, 1, SamplingMode.BOOTSTRAP, False, False),
])
def test_config_validation(bad: MonteCarloConfig) -> None:
    with pytest.raises(ValueError):
        run_monte_carlo(source(), bad)


def test_reports_are_canonical_and_stable() -> None:
    result = run_monte_carlo(source(), config())
    assert monte_carlo_to_json(result) == monte_carlo_to_json(run_monte_carlo(source(), config()))
    assert monte_carlo_to_text(result) == monte_carlo_to_text(run_monte_carlo(source(), config()))
    assert '"mean":"' in monte_carlo_to_json(result)


def test_experiment_suite_adapter_orders_by_experiment_id() -> None:
    analytics = source()
    suite = ExperimentSuiteAnalyticsResult("dataset", (
        ExperimentAnalyticsResult("z", "z-config", analytics),
        ExperimentAnalyticsResult("a", "a-config", analytics),
    ))
    result = run_monte_carlo(suite, config())
    assert isinstance(result, ExperimentSuiteMonteCarloResult)
    assert tuple(item.experiment_id for item in result.experiment_results) == ("a", "z")


def test_walk_forward_adapter_orders_windows_and_experiments() -> None:
    analytics = source()
    completed = WalkForwardAnalyticsResult("dataset", (
        WalkForwardWindowExperimentAnalytics(2, "z", analytics),
        WalkForwardWindowExperimentAnalytics(1, "a", analytics),
    ), (), ())
    result = run_monte_carlo(completed, config())
    assert isinstance(result, WalkForwardMonteCarloResult)
    assert tuple((item.window_index, item.experiment_id) for item in result.window_results) == ((1, "a"), (2, "z"))


def test_package_has_no_execution_or_runner_imports() -> None:
    package = Path(__file__).parents[1] / "app" / "monte_carlo"
    content = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    forbidden = ("app.paper_trading", "app.backtesting", "app.strategy", "app.risk",
                 "app.compliance", "app.order_compliance", "app.ai")
    assert not any(f"from {name}" in content or f"import {name}" in content for name in forbidden)
