from __future__ import annotations

from decimal import Decimal

from app.paper_trading.models import JournalEventType
from app.walkforward.models import WalkForwardAggregate, WalkForwardRun

ZERO = Decimal(0)
HUNDRED = Decimal(100)


def aggregate_walk_forward(runs: tuple[WalkForwardRun, ...]) -> tuple[WalkForwardAggregate, ...]:
    if not runs:
        raise ValueError("walk-forward runs must not be empty")
    ids = tuple(item.experiment_id for item in runs[0].experiment_results.experiment_results)
    if any(tuple(item.experiment_id for item in run.experiment_results.experiment_results) != ids for run in runs):
        raise ValueError("every window must contain the same ordered experiment IDs")
    aggregates: list[WalkForwardAggregate] = []
    for experiment_id in ids:
        experiment_results = tuple(
            next(item for item in run.experiment_results.experiment_results if item.experiment_id == experiment_id)
            for run in runs
        )
        comparison_rows = tuple(
            next(item for item in run.experiment_results.comparison_rows if item.experiment_id == experiment_id)
            for run in runs
        )
        compounded = Decimal(1)
        outcomes: list[Decimal] = []
        for result in experiment_results:
            compounded *= Decimal(1) + result.backtest_result.total_return / HUNDRED
            for event in result.backtest_result.checkpoint.paper_journal.events:
                details = dict(event.details)
                if event.event_type is JournalEventType.FILL and "realized_pnl" in details:
                    value = Decimal(details["realized_pnl"])
                    if value != ZERO:
                        outcomes.append(value)
        winners = tuple(value for value in outcomes if value > ZERO)
        losers = tuple(value for value in outcomes if value < ZERO)
        count = len(outcomes)
        aggregates.append(
            WalkForwardAggregate(
                experiment_id, len(runs), (compounded - Decimal(1)) * HUNDRED,
                max(item.backtest_result.maximum_drawdown for item in experiment_results),
                Decimal(len(winners)) / Decimal(count) * HUNDRED if count else ZERO,
                sum(winners, ZERO) / abs(sum(losers, ZERO)) if losers else None,
                sum(outcomes, ZERO) / Decimal(count) if count else None,
                sum(item.number_of_trades for item in comparison_rows),
                sum(item.number_of_rejected_proposals for item in comparison_rows),
                sum(item.number_of_gfv_rejections for item in comparison_rows),
                sum(item.number_of_compliance_rejections for item in comparison_rows),
                tuple(item.configuration_fingerprint for item in experiment_results),
            )
        )
    return tuple(aggregates)
