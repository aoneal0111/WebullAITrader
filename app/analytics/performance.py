from __future__ import annotations

from decimal import Decimal

from app.analytics.distribution import (
    analyze_distribution, group_realized_pnl, median, rolling_return_metric, rolling_trade_metric,
)
from app.analytics.equity import (
    analyze_equity, calculate_daily_returns, calculate_monthly_returns, calculate_weekly_returns,
)
from app.analytics.exposure import analyze_exposure
from app.analytics.models import (
    AnalyticsConfig, BacktestAnalyticsResult, ExperimentAnalyticsResult,
    ExperimentSuiteAnalyticsResult, TradeAnalytics, TradeOutcome,
    WalkForwardAnalyticsResult, WalkForwardExperimentAggregateAnalytics,
    WalkForwardWindowExperimentAnalytics,
)
from app.analytics.risk import analyze_risk
from app.backtesting.models import canonical_fingerprint
from app.backtesting.results import BacktestResult
from app.experiments.models import ExperimentResult, ExperimentSuiteResult
from app.paper_trading.models import JournalEventType
from app.walkforward.models import WalkForwardResult

ZERO = Decimal(0)
HUNDRED = Decimal(100)


def analyze_backtest(
    result: BacktestResult, config: AnalyticsConfig = AnalyticsConfig()
) -> BacktestAnalyticsResult:
    if not isinstance(result, BacktestResult):
        raise ValueError("BacktestResult is required")
    checkpoint = result.checkpoint
    equity = analyze_equity(checkpoint.equity_curve)
    daily = calculate_daily_returns(checkpoint.equity_curve)
    weekly = calculate_weekly_returns(checkpoint.equity_curve)
    monthly = calculate_monthly_returns(checkpoint.equity_curve)
    outcomes, outcome_warnings = _extract_outcomes(checkpoint.paper_journal.events)
    trades = analyze_trades(outcomes)
    exposure = analyze_exposure(getattr(checkpoint, "portfolio_history", None), checkpoint.equity_curve)
    warnings = [*outcome_warnings, *exposure.prerequisites]
    rolling_window = config.rolling_window
    source_identity = canonical_fingerprint((checkpoint.schema_version, checkpoint.dataset_fingerprint,
                                             checkpoint.response_fingerprint, checkpoint.intent_fingerprint,
                                             checkpoint.config_fingerprint))
    return BacktestAnalyticsResult(
        source_identity, checkpoint.dataset_fingerprint, checkpoint.config_fingerprint,
        checkpoint.response_fingerprint, checkpoint.intent_fingerprint, equity,
        analyze_risk(equity.return_observations, equity, result.start_timestamp, result.end_timestamp, config),
        exposure, trades,
        analyze_distribution(tuple(item.return_value for item in equity.return_observations)),
        analyze_distribution(tuple(item.return_value for item in daily)),
        analyze_distribution(tuple(item.return_value for item in weekly)),
        analyze_distribution(tuple(item.return_value for item in monthly)),
        group_realized_pnl(outcomes, "month"), group_realized_pnl(outcomes, "weekday"),
        group_realized_pnl(outcomes, "hour"),
        rolling_trade_metric(outcomes, rolling_window, "win_rate") if rolling_window else (),
        rolling_trade_metric(outcomes, rolling_window, "expectancy") if rolling_window else (),
        rolling_return_metric(equity.return_observations, rolling_window, "mean") if rolling_window else (),
        rolling_return_metric(equity.return_observations, rolling_window, "volatility") if rolling_window else (),
        tuple(dict.fromkeys(warnings)), completed_trade_outcomes=outcomes,
        market_observations=tuple(sorted(getattr(checkpoint, "market_observations", ()),
                                         key=lambda item: (item.timestamp, item.symbol))),
    )


def analyze_experiment(
    result: ExperimentResult, config: AnalyticsConfig = AnalyticsConfig()
) -> ExperimentAnalyticsResult:
    return ExperimentAnalyticsResult(result.experiment_id, result.configuration_fingerprint,
                                     analyze_backtest(result.backtest_result, config))


def analyze_experiment_suite(
    result: ExperimentSuiteResult, config: AnalyticsConfig = AnalyticsConfig()
) -> ExperimentSuiteAnalyticsResult:
    return ExperimentSuiteAnalyticsResult(
        result.dataset_fingerprint,
        tuple(analyze_experiment(item, config) for item in sorted(result.experiment_results, key=lambda item: item.experiment_id)),
    )


def analyze_walk_forward(
    result: WalkForwardResult, config: AnalyticsConfig = AnalyticsConfig()
) -> WalkForwardAnalyticsResult:
    windows = []
    grouped: dict[str, list[BacktestAnalyticsResult]] = {}
    for run in sorted(result.runs, key=lambda item: item.window_index):
        for experiment in sorted(run.experiment_results.experiment_results, key=lambda item: item.experiment_id):
            analytics = analyze_backtest(experiment.backtest_result, config)
            windows.append(WalkForwardWindowExperimentAnalytics(run.window_index, experiment.experiment_id, analytics))
            grouped.setdefault(experiment.experiment_id, []).append(analytics)
    aggregates = []
    for aggregate in sorted(result.aggregates, key=lambda item: item.experiment_id):
        analytics_items = grouped[aggregate.experiment_id]
        # Outcomes are read again from completed window journals to retain exact close timestamps.
        exact = []
        for run in result.runs:
            experiment = next(item for item in run.experiment_results.experiment_results if item.experiment_id == aggregate.experiment_id)
            extracted, _ = _extract_outcomes(experiment.backtest_result.checkpoint.paper_journal.events)
            exact.extend(extracted)
        return_values = tuple(item.equity.total_return for item in analytics_items)
        aggregates.append(
            WalkForwardExperimentAggregateAnalytics(
                aggregate.experiment_id, aggregate.aggregate_return, aggregate.aggregate_drawdown,
                analyze_trades(tuple(exact)), analyze_distribution(return_values), False,
            )
        )
    return WalkForwardAnalyticsResult(
        result.source_dataset_fingerprint, tuple(windows), tuple(aggregates),
        ("Independent window equity curves were not stitched; continuous-equity risk ratios are unavailable.",),
    )


def analyze_trades(outcomes: tuple[TradeOutcome, ...]) -> TradeAnalytics:
    ordered = tuple(sorted(outcomes, key=lambda item: (item.close_timestamp, item.journal_sequence)))
    winners = tuple(item.realized_pnl for item in ordered if item.is_win)
    losers = tuple(item.realized_pnl for item in ordered if item.is_loss)
    breakevens = sum(item.is_breakeven for item in ordered)
    directional = len(winners) + len(losers)
    gross_profit = sum(winners, ZERO)
    gross_loss = abs(sum(losers, ZERO))
    average_winner = gross_profit / Decimal(len(winners)) if winners else None
    average_loser = sum(losers, ZERO) / Decimal(len(losers)) if losers else None
    return TradeAnalytics(
        len(ordered), len(winners), len(losers), breakevens,
        Decimal(len(winners)) / Decimal(directional) * HUNDRED if directional else None,
        Decimal(len(losers)) / Decimal(directional) * HUNDRED if directional else None,
        average_winner, average_loser, max(winners) if winners else None, min(losers) if losers else None,
        median(tuple(item.realized_pnl for item in ordered)), gross_profit, gross_loss,
        gross_profit / gross_loss if gross_loss else None,
        sum((item.realized_pnl for item in ordered), ZERO) / Decimal(len(ordered)) if ordered else None,
        average_winner / abs(average_loser) if average_winner is not None and average_loser is not None else None,
        _max_streak(ordered, "win"), _max_streak(ordered, "loss"),
    )


def _extract_outcomes(events) -> tuple[tuple[TradeOutcome, ...], tuple[str, ...]]:
    outcomes = []
    warnings = []
    for event in events:
        if event.event_type is not JournalEventType.FILL:
            continue
        details = dict(event.details)
        side = details.get("side")
        if side is None:
            warnings.append("Fill side is missing; breakeven SELL outcomes cannot be classified.")
            if Decimal(details.get("realized_pnl", "0")) == ZERO:
                continue
        if side not in (None, "SELL"):
            continue
        pnl = Decimal(details["realized_pnl"])
        outcomes.append(TradeOutcome(event.timestamp, pnl, pnl > ZERO, pnl < ZERO, pnl == ZERO,
                                     None, None, details.get("symbol"), event.request_id, event.sequence))
    return tuple(sorted(outcomes, key=lambda item: (item.close_timestamp, item.journal_sequence))), tuple(warnings)


def _max_streak(outcomes: tuple[TradeOutcome, ...], kind: str) -> int:
    best = current = 0
    for outcome in outcomes:
        matches = outcome.is_win if kind == "win" else outcome.is_loss
        current = current + 1 if matches else 0
        best = max(best, current)
    return best
