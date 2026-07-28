from __future__ import annotations

from .models import (
    ComparisonSnapshot,
    ExperimentResult,
    PlaybackStatus,
)
from .statistics import compare_metric


class ComparisonEngine:
    def compare(
        self,
        baseline: ExperimentResult,
        candidate: ExperimentResult,
    ) -> ComparisonSnapshot:
        for value, name in (
            (baseline, "baseline"),
            (candidate, "candidate"),
        ):
            if not isinstance(value, ExperimentResult):
                raise TypeError(f"{name} must be ExperimentResult")
            if value.playback_status is not PlaybackStatus.COMPLETED:
                raise ValueError(f"{name} experiment must be completed")
        first = baseline.analytics
        second = candidate.analytics
        return ComparisonSnapshot(
            baseline.experiment.experiment_id,
            candidate.experiment.experiment_id,
            (
                compare_metric(
                    "total_trades",
                    first.performance.total_trades,
                    second.performance.total_trades,
                ),
                compare_metric(
                    "winning_trades",
                    first.performance.winning_trades,
                    second.performance.winning_trades,
                ),
                compare_metric(
                    "losing_trades",
                    first.performance.losing_trades,
                    second.performance.losing_trades,
                ),
                compare_metric(
                    "win_rate",
                    first.performance.win_rate,
                    second.performance.win_rate,
                ),
                compare_metric(
                    "net_realized_pnl",
                    first.performance.net_realized_pnl,
                    second.performance.net_realized_pnl,
                ),
                compare_metric(
                    "gross_profit",
                    first.performance.gross_profit,
                    second.performance.gross_profit,
                ),
                compare_metric(
                    "gross_loss",
                    first.performance.gross_loss,
                    second.performance.gross_loss,
                ),
                compare_metric(
                    "profit_factor",
                    first.performance.profit_factor,
                    second.performance.profit_factor,
                ),
                compare_metric(
                    "expectancy",
                    first.performance.expectancy,
                    second.performance.expectancy,
                ),
                compare_metric(
                    "maximum_drawdown",
                    first.risk.maximum_drawdown,
                    second.risk.maximum_drawdown,
                ),
                compare_metric(
                    "recovery_factor",
                    first.risk.recovery_factor,
                    second.risk.recovery_factor,
                ),
                compare_metric(
                    "average_holding_duration",
                    first.performance.average_holding_duration,
                    second.performance.average_holding_duration,
                ),
            ),
        )
