from __future__ import annotations

from app.backtesting.models import ReplayEventType
from app.experiments.models import ComparisonRow, ExperimentResult


def build_comparison(results: tuple[ExperimentResult, ...]) -> tuple[ComparisonRow, ...]:
    rows: list[ComparisonRow] = []
    for result in sorted(results, key=lambda item: item.experiment_id):
        backtest = result.backtest_result
        events = backtest.checkpoint.replay_journal.events
        gfv_rejections = sum(
            event.event_type is ReplayEventType.GFV and event.status == "REJECTED" for event in events
        )
        compliance_rejections = sum(
            event.event_type is ReplayEventType.ORDER_COMPLIANCE
            and event.status in ("REJECTED", "REJECTED_INTENT")
            for event in events
        )
        rows.append(
            ComparisonRow(
                result.experiment_id, backtest.total_return, backtest.maximum_drawdown,
                backtest.win_rate, backtest.profit_factor, backtest.expectancy,
                backtest.number_filled, backtest.number_rejected, gfv_rejections,
                compliance_rejections, result.dataset_fingerprint,
                result.configuration_fingerprint, result.runtime,
            )
        )
    return tuple(rows)
