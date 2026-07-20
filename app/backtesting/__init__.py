"""Deterministic historical replay over the production analysis pipeline."""

from app.backtesting.models import *
from app.backtesting.report import render_text_report
from app.backtesting.results import BacktestResult, checkpoint_from_json
from app.backtesting.runner import resume_backtest, run_backtest, run_until

__all__ = ["BacktestResult", "checkpoint_from_json", "render_text_report", "resume_backtest", "run_backtest", "run_until"]

from app.backtesting.exit_comparison import (
    BacktestExitComparison,
    ExitStrategyMetrics,
    HistoricalExitMethod,
    HistoricalTradeResult,
    calculate_exit_strategy_metrics,
    compare_historical_exit_methods,
    evaluate_fixed_target_entries,
    evaluate_trailing_entries,
)

__all__ += [
    "BacktestExitComparison",
    "ExitStrategyMetrics",
    "HistoricalExitMethod",
    "HistoricalTradeResult",
    "calculate_exit_strategy_metrics",
    "compare_historical_exit_methods",
    "evaluate_fixed_target_entries",
    "evaluate_trailing_entries",
]
