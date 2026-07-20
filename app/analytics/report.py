from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from app.analytics.models import (
    BacktestAnalyticsResult,
    ExperimentAnalyticsResult,
    ExperimentSuiteAnalyticsResult,
    WalkForwardAnalyticsResult,
)

WARNING = "HISTORICAL PAPER-SIMULATION ANALYTICS ONLY — NO OPTIMIZATION, WINNER SELECTION, OR LIVE EXECUTION"
SCHEMA_VERSION = "1.0"


def analytics_to_json(value: object) -> str:
    _validate_result(value)
    payload = {"analytics": _json_safe(value), "schema_version": SCHEMA_VERSION, "warning": WARNING}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def analytics_to_text(value: object) -> str:
    _validate_result(value)
    lines = [WARNING, f"Schema version: {SCHEMA_VERSION}"]
    if isinstance(value, BacktestAnalyticsResult):
        lines.extend(_backtest_lines(value))
    elif isinstance(value, ExperimentAnalyticsResult):
        lines.extend((f"EXPERIMENT {value.experiment_id}", f"Configuration fingerprint: {value.configuration_fingerprint}"))
        lines.extend(_backtest_lines(value.backtest_analytics))
    elif isinstance(value, ExperimentSuiteAnalyticsResult):
        lines.extend(("EXPERIMENT SUITE", f"Dataset fingerprint: {value.dataset_fingerprint}"))
        for item in value.experiment_results:
            lines.extend((f"EXPERIMENT {item.experiment_id}", f"Configuration fingerprint: {item.configuration_fingerprint}"))
            lines.extend(_backtest_lines(item.backtest_analytics))
    else:
        lines.extend(("WALK-FORWARD ANALYTICS", f"Dataset fingerprint: {value.source_dataset_fingerprint}"))
        for item in value.window_results:
            lines.append(f"WINDOW {item.window_index} / EXPERIMENT {item.experiment_id}")
            lines.extend(_backtest_lines(item.analytics))
        lines.append("OUT-OF-SAMPLE AGGREGATES")
        for item in value.experiment_aggregates:
            lines.extend((
                f"Experiment: {item.experiment_id}",
                f"Compounded return (decimal fraction): {_display(item.compounded_return)}",
                f"Maximum window drawdown (decimal fraction): {_display(item.maximum_window_drawdown)}",
                f"Continuous equity risk available: {str(item.continuous_equity_risk_available).lower()}",
            ))
        lines.extend(f"Warning: {warning}" for warning in value.warnings)
    return "\n".join(lines) + "\n"


def _backtest_lines(value: BacktestAnalyticsResult) -> list[str]:
    equity, risk, exposure, trades = value.equity, value.risk, value.exposure, value.trades
    return [
        "EQUITY",
        f"Starting equity: {_display(equity.starting_equity)}",
        f"Ending equity: {_display(equity.ending_equity)}",
        f"Total return (decimal fraction): {_display(equity.total_return)}",
        "DRAWDOWN",
        f"Maximum drawdown (decimal fraction): {_display(equity.maximum_drawdown)}",
        f"Average episode drawdown (decimal fraction): {_display(equity.average_drawdown)}",
        f"Current drawdown (decimal fraction): {_display(equity.current_drawdown)}",
        "RISK",
        f"Mean return ({risk.return_interval}, decimal fraction): {_display(risk.arithmetic_mean_return)}",
        f"Population volatility ({risk.return_interval}): {_display(risk.return_standard_deviation)}",
        f"Sharpe ratio (per-period): {_display(risk.period_sharpe_ratio)}",
        f"Sharpe ratio (annualized): {_display(risk.annualized_sharpe_ratio)}",
        f"Sortino ratio (per-period): {_display(risk.period_sortino_ratio)}",
        f"Sortino ratio (annualized): {_display(risk.annualized_sortino_ratio)}",
        f"Annualized return: {_display(risk.annualized_return)}",
        f"Annualized volatility: {_display(risk.annualized_volatility)}",
        f"Calmar ratio: {_display(risk.calmar_ratio)}",
        "EXPOSURE",
        f"Available: {str(exposure.available).lower()}",
        f"Time in market (percent): {_display(exposure.time_in_market_percent)}",
        f"Average gross exposure (percent): {_display(exposure.average_gross_exposure_percent)}",
        f"Maximum gross exposure (percent): {_display(exposure.maximum_gross_exposure_percent)}",
        "TRADES (REALIZED SELL-FILL OUTCOMES)",
        f"Completed outcomes: {trades.total_completed_outcomes}",
        f"Win rate (percent of non-breakeven outcomes): {_display(trades.win_rate)}",
        f"Profit factor: {_display(trades.profit_factor)}",
        f"Expectancy (currency per realized fill): {_display(trades.expectancy)}",
        "DISTRIBUTIONS",
        f"Point-return observations: {value.return_distribution.count}",
        f"Daily return observations: {value.daily_return_distribution.count}",
        f"Weekly return observations: {value.weekly_return_distribution.count}",
        f"Monthly return observations: {value.monthly_return_distribution.count}",
        *[f"Prerequisite/Warning: {warning}" for warning in value.warnings],
    ]


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_safe(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return value


def _display(value: object) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _validate_result(value: object) -> None:
    if not isinstance(value, (BacktestAnalyticsResult, ExperimentAnalyticsResult,
                              ExperimentSuiteAnalyticsResult, WalkForwardAnalyticsResult)):
        raise ValueError("a completed analytics result is required")
