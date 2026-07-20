from __future__ import annotations

from datetime import UTC
from decimal import Decimal

from app.analytics import (
    AnalyticsConfig, BacktestAnalyticsResult, ExperimentAnalyticsResult, ExperimentSuiteAnalyticsResult,
    WalkForwardAnalyticsResult, analyze_backtest, analyze_experiment, analyze_experiment_suite,
    analyze_walk_forward,
)
from app.analytics.equity import analyze_equity, equity_curve_from_returns, equity_curve_from_trade_outcomes
from app.analytics.performance import analyze_trades
from app.analytics.risk import analyze_risk
from app.stress_testing.models import (
    ComparisonThreshold, ExperimentStressTestResult, ExperimentSuiteStressTestResult, ScenarioFilter, ScenarioKind,
    ScenarioMetrics, ScenarioResult, StressTestConfig, StressTestResult, WalkForwardStressTestItem,
    WalkForwardStressTestResult,
)
from app.stress_testing.scenarios import effective_filter, prerequisite
from app.stress_testing.statistics import compare_metric

ZERO = Decimal(0)


def run_stress_test(source: object, config: StressTestConfig) -> object:
    _validate_config(config)
    normalized = _normalize(source)
    if isinstance(normalized, BacktestAnalyticsResult):
        return _run_atomic(normalized, config)
    if isinstance(normalized, ExperimentAnalyticsResult):
        return ExperimentStressTestResult(normalized.experiment_id, normalized.configuration_fingerprint,
                                          _run_atomic(normalized.backtest_analytics, config))
    if isinstance(normalized, ExperimentSuiteAnalyticsResult):
        return ExperimentSuiteStressTestResult(
            normalized.dataset_fingerprint,
            tuple(ExperimentStressTestResult(item.experiment_id, item.configuration_fingerprint,
                                             _run_atomic(item.backtest_analytics, config))
                  for item in sorted(normalized.experiment_results, key=lambda item: item.experiment_id)),
        )
    if isinstance(normalized, WalkForwardAnalyticsResult):
        return WalkForwardStressTestResult(
            normalized.source_dataset_fingerprint,
            tuple(WalkForwardStressTestItem(item.window_index, item.experiment_id,
                                            _run_atomic(item.analytics, config))
                  for item in sorted(normalized.window_results, key=lambda item: (item.window_index, item.experiment_id))),
        )
    raise ValueError("unsupported completed result")


def _run_atomic(source: BacktestAnalyticsResult, config: StressTestConfig) -> StressTestResult:
    results = tuple(_evaluate(source, effective_filter(kind, config.custom_filters), config)
                    for kind in sorted(set(config.enabled_scenarios), key=lambda item: item.value))
    return StressTestResult(source.source_identity, results)


def _evaluate(source: BacktestAnalyticsResult, filter_: ScenarioFilter,
              config: StressTestConfig) -> ScenarioResult:
    missing = prerequisite(filter_)
    if missing:
        return _unavailable(filter_.scenario, missing)
    observation_required = _requires_market_observation(filter_)
    observations = source.market_observations
    if observation_required and not observations:
        return _unavailable(filter_.scenario, "authoritative market-observation history is required")
    drawdowns = _drawdown_by_timestamp(source)
    selected = tuple(item for item in observations if _match_observation(item, filter_, drawdowns))
    if observation_required and not selected:
        fields_missing = _missing_authoritative_field(observations, filter_)
        warning = fields_missing or "no authoritative observations matched the explicit scenario filter"
        return _unavailable(filter_.scenario, warning)
    timestamps = frozenset(item.timestamp for item in selected) if observation_required else None
    returns = tuple(item for item in source.equity.return_observations
                    if _match_timestamp(item.timestamp, filter_)
                    and _match_return_drawdown(item.timestamp, filter_, drawdowns)
                    and (timestamps is None or item.timestamp in timestamps))
    trades = tuple(item for item in source.completed_trade_outcomes
                   if _match_timestamp(item.close_timestamp, filter_)
                   and _match_return_drawdown(item.close_timestamp, filter_, drawdowns)
                   and (filter_.symbol is None or item.symbol == filter_.symbol.upper())
                   and _match_outcome(item.realized_pnl, filter_.outcome)
                   and (timestamps is None or item.close_timestamp in timestamps))
    if not returns and not trades:
        return _unavailable(filter_.scenario, "no completed return or trade observations matched the scenario")
    curve = _scenario_curve(source, returns, trades)
    equity = analyze_equity(curve)
    trade_analytics = analyze_trades(trades)
    observed_timestamps = tuple(item.timestamp for item in returns) if returns else tuple(item.close_timestamp for item in trades)
    risk = analyze_risk(equity.return_observations, equity, observed_timestamps[0], observed_timestamps[-1],
                        AnalyticsConfig(annualization_periods=config.annualization_periods))
    exposure_warning = ()
    average_exposure = time_in_market = None
    if config.include_exposure_metrics:
        if _is_unfiltered_custom(filter_) and source.exposure.available:
            average_exposure = source.exposure.average_gross_exposure_percent
            time_in_market = source.exposure.time_in_market_percent
        else:
            exposure_warning = ("Scenario-aligned portfolio snapshots are unavailable; exposure metrics fail closed.",)
    metrics = ScenarioMetrics(
        equity.total_return, risk.annualized_return, equity.maximum_drawdown, trade_analytics.win_rate,
        trade_analytics.loss_rate, trade_analytics.profit_factor, trade_analytics.expectancy,
        _preferred(risk.annualized_sharpe_ratio, risk.period_sharpe_ratio),
        _preferred(risk.annualized_sortino_ratio, risk.period_sortino_ratio), risk.calmar_ratio,
        trade_analytics.total_completed_outcomes, average_exposure, time_in_market,
        trade_analytics.maximum_consecutive_wins, trade_analytics.maximum_consecutive_losses,
    )
    comparisons = _comparisons(source, metrics, filter_, config)
    return ScenarioResult(filter_.scenario, True, len(selected) if observation_required else len(returns) + len(trades),
                          metrics, comparisons, exposure_warning)


def _scenario_curve(source, returns, trades):
    return (equity_curve_from_returns(source.equity.starting_equity, returns) if returns
            else equity_curve_from_trade_outcomes(source.equity.starting_equity, trades))


def _comparisons(source, metrics, filter_, config):
    original = {
        "total_return": source.equity.total_return, "cagr": source.risk.annualized_return,
        "maximum_drawdown": source.equity.maximum_drawdown,
        "win_rate": source.trades.win_rate, "loss_rate": source.trades.loss_rate,
        "profit_factor": source.trades.profit_factor, "expectancy": source.trades.expectancy,
        "sharpe": _preferred(source.risk.annualized_sharpe_ratio, source.risk.period_sharpe_ratio),
        "sortino": _preferred(source.risk.annualized_sortino_ratio, source.risk.period_sortino_ratio),
        "calmar": source.risk.calmar_ratio, "number_of_trades": Decimal(source.trades.total_completed_outcomes),
        "average_gross_exposure_percent": source.exposure.average_gross_exposure_percent,
        "time_in_market_percent": source.exposure.time_in_market_percent,
        "maximum_consecutive_wins": Decimal(source.trades.maximum_consecutive_wins),
        "maximum_consecutive_losses": Decimal(source.trades.maximum_consecutive_losses),
    }
    return tuple(compare_metric(name, original[name], Decimal(getattr(metrics, name)) if isinstance(getattr(metrics, name), int) else getattr(metrics, name), config.comparison_tolerance,
                                filter_.thresholds) for name in sorted(original))


def _match_timestamp(timestamp, filter_) -> bool:
    utc = timestamp.astimezone(UTC)
    return not ((filter_.start_timestamp and timestamp < filter_.start_timestamp)
                or (filter_.end_timestamp and timestamp > filter_.end_timestamp)
                or (filter_.weekdays and utc.isoweekday() not in filter_.weekdays)
                or (filter_.months and utc.month not in filter_.months)
                or (filter_.hours and utc.hour not in filter_.hours))


def _match_return_drawdown(timestamp, filter_, drawdowns):
    value = drawdowns.get(timestamp)
    return not ((filter_.minimum_drawdown is not None and (value is None or value < filter_.minimum_drawdown))
                or (filter_.maximum_drawdown is not None and (value is None or value > filter_.maximum_drawdown)))


def _match_observation(item, filter_, drawdowns) -> bool:
    if not _match_timestamp(item.timestamp, filter_): return False
    if filter_.symbol and item.symbol != filter_.symbol.upper(): return False
    if filter_.volatility_regime and item.volatility_regime != filter_.volatility_regime: return False
    if filter_.trend_regime and item.trend_regime != filter_.trend_regime: return False
    if filter_.session and item.session != filter_.session: return False
    if filter_.scenario is ScenarioKind.TRADING_HALTS and item.market_status != "HALTED": return False
    drawdown = drawdowns.get(item.timestamp)
    if filter_.minimum_drawdown is not None and (drawdown is None or drawdown < filter_.minimum_drawdown): return False
    if filter_.maximum_drawdown is not None and (drawdown is None or drawdown > filter_.maximum_drawdown): return False
    if filter_.minimum_absolute_slippage is not None and (item.observed_slippage is None or abs(item.observed_slippage) < filter_.minimum_absolute_slippage): return False
    return True


def _drawdown_by_timestamp(source):
    equity = source.equity.starting_equity
    peak = equity
    result = {}
    for item in source.equity.return_observations:
        equity *= Decimal(1) + item.return_value
        peak = max(peak, equity)
        result[item.timestamp] = Decimal(1) - equity / peak
    return result


def _requires_market_observation(filter_):
    if filter_.scenario in (ScenarioKind.CUSTOM, ScenarioKind.MARKET_CRASH):
        return any((filter_.volatility_regime, filter_.trend_regime, filter_.session,
                    filter_.minimum_absolute_slippage))
    return True


def _missing_authoritative_field(observations, filter_):
    checks = ((filter_.volatility_regime, "volatility regime", "volatility_regime"),
              (filter_.trend_regime, "trend regime", "trend_regime"), (filter_.session, "session", "session"),
              (filter_.minimum_absolute_slippage, "observed slippage", "observed_slippage"))
    for active, label, field in checks:
        if active is not None and all(getattr(item, field) is None for item in observations):
            return f"authoritative {label} observations are required"
    return None


def _match_outcome(value, requested):
    return requested is None or requested == "WIN" and value > ZERO or requested == "LOSS" and value < ZERO or requested == "BREAKEVEN" and value == ZERO


def _unavailable(kind, warning):
    return ScenarioResult(kind, False, 0, None, (), (warning,))


def _preferred(annualized, period):
    return annualized if annualized is not None else period


def _normalize(source):
    if isinstance(source, (BacktestAnalyticsResult, ExperimentAnalyticsResult, ExperimentSuiteAnalyticsResult, WalkForwardAnalyticsResult)):
        return source
    name = type(source).__name__
    if name == "BacktestResult": return analyze_backtest(source)
    if name == "ExperimentResult": return analyze_experiment(source)
    if name == "ExperimentSuiteResult": return analyze_experiment_suite(source)
    if name == "WalkForwardResult": return analyze_walk_forward(source)
    raise ValueError("a supported completed result is required")


def _validate_config(config):
    if not isinstance(config, StressTestConfig): raise ValueError("StressTestConfig is required")
    if not isinstance(config.enabled_scenarios, tuple) or not config.enabled_scenarios or any(not isinstance(item, ScenarioKind) for item in config.enabled_scenarios): raise ValueError("enabled_scenarios are required")
    if len(set(config.enabled_scenarios)) != len(config.enabled_scenarios): raise ValueError("enabled scenarios must be unique")
    if not isinstance(config.comparison_tolerance, Decimal) or not config.comparison_tolerance.is_finite() or config.comparison_tolerance < ZERO: raise ValueError("comparison_tolerance must be nonnegative")
    if config.annualization_periods is not None and (not isinstance(config.annualization_periods, int) or isinstance(config.annualization_periods, bool) or config.annualization_periods <= 0): raise ValueError("annualization_periods must be positive")
    if not isinstance(config.include_exposure_metrics, bool): raise ValueError("include_exposure_metrics must be boolean")
    if not isinstance(config.custom_filters, tuple): raise ValueError("custom_filters must be a tuple")
    for filter_ in config.custom_filters:
        _validate_filter(filter_)


def _validate_filter(filter_):
    if not isinstance(filter_, ScenarioFilter): raise ValueError("custom filters must be ScenarioFilter values")
    if not isinstance(filter_.scenario, ScenarioKind): raise ValueError("filter scenario is invalid")
    if filter_.start_timestamp is not None and filter_.start_timestamp.tzinfo is None: raise ValueError("filter timestamps must be timezone-aware")
    if filter_.end_timestamp is not None and filter_.end_timestamp.tzinfo is None: raise ValueError("filter timestamps must be timezone-aware")
    if filter_.start_timestamp and filter_.end_timestamp and filter_.start_timestamp > filter_.end_timestamp: raise ValueError("filter date range is invalid")
    for values, lower, upper, label in ((filter_.weekdays, 1, 7, "weekdays"), (filter_.months, 1, 12, "months"), (filter_.hours, 0, 23, "hours")):
        if len(set(values)) != len(values) or any(not isinstance(item, int) or isinstance(item, bool) or not lower <= item <= upper for item in values): raise ValueError(f"{label} are invalid")
    decimals = (filter_.minimum_drawdown, filter_.maximum_drawdown, filter_.maximum_volume,
                filter_.minimum_gap_percent, filter_.minimum_spread, filter_.minimum_absolute_slippage)
    if any(item is not None and (not isinstance(item, Decimal) or not item.is_finite() or item < ZERO) for item in decimals): raise ValueError("filter numeric boundaries must be finite nonnegative Decimals")
    if filter_.minimum_drawdown is not None and filter_.maximum_drawdown is not None and filter_.minimum_drawdown > filter_.maximum_drawdown: raise ValueError("drawdown range is invalid")
    if filter_.outcome not in (None, "WIN", "LOSS", "BREAKEVEN"): raise ValueError("outcome filter is invalid")
    for label in (filter_.volatility_regime, filter_.trend_regime, filter_.session, filter_.symbol):
        if label is not None and (not isinstance(label, str) or not label.strip()): raise ValueError("filter labels must be nonempty strings")
    metrics = set()
    supported_metrics = {"total_return", "cagr", "maximum_drawdown", "win_rate", "loss_rate",
                         "profit_factor", "expectancy", "sharpe", "sortino", "calmar",
                         "number_of_trades", "average_gross_exposure_percent", "time_in_market_percent",
                         "maximum_consecutive_wins", "maximum_consecutive_losses"}
    for threshold in filter_.thresholds:
        if not isinstance(threshold, ComparisonThreshold): raise ValueError("thresholds must be ComparisonThreshold values")
        if threshold.metric in metrics or threshold.metric not in supported_metrics: raise ValueError("comparison threshold metrics must be unique and supported")
        if not isinstance(threshold.maximum_adverse_difference, Decimal) or not threshold.maximum_adverse_difference.is_finite() or threshold.maximum_adverse_difference < ZERO: raise ValueError("comparison thresholds must be finite and nonnegative")
        metrics.add(threshold.metric)


def _is_unfiltered_custom(filter_):
    return filter_.scenario is ScenarioKind.CUSTOM and all((
        filter_.start_timestamp is None, filter_.end_timestamp is None, filter_.volatility_regime is None,
        filter_.trend_regime is None, filter_.session is None, filter_.symbol is None,
        not filter_.weekdays, not filter_.months, not filter_.hours, filter_.minimum_drawdown is None,
        filter_.maximum_drawdown is None, filter_.outcome is None,
    ))
