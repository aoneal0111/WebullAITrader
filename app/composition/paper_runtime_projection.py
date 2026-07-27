from __future__ import annotations

from collections.abc import Callable

from app.operations import PaperRuntimeCycleResult
from app.operations_core import (
    OperationsBus,
    PaperRuntimeSnapshot,
    PaperRuntimeUpdated,
)


PaperRuntimeResultSink = Callable[[PaperRuntimeCycleResult], None]


def create_paper_runtime_snapshot(
    result: PaperRuntimeCycleResult,
) -> PaperRuntimeSnapshot:
    """Map a completed paper-runtime cycle into presentation-safe state."""

    if not isinstance(result, PaperRuntimeCycleResult):
        raise TypeError(
            "result must be a PaperRuntimeCycleResult"
        )

    statistics = result.session.statistics
    metrics = result.session.metrics

    return PaperRuntimeSnapshot(
        cycle=result.cycle,
        timestamp=result.timestamp,
        session_id=result.session.session_id,
        symbols=result.symbols,
        decisions_processed=statistics.decisions_processed,
        orders_attempted=statistics.orders_attempted,
        orders_filled=statistics.orders_filled,
        orders_rejected=statistics.orders_rejected,
        orders_not_filled=statistics.orders_not_filled,
        decisions_skipped=statistics.decisions_skipped,
        winning_fills=statistics.winning_fills,
        losing_fills=statistics.losing_fills,
        breakeven_fills=statistics.breakeven_fills,
        realized_pnl=statistics.realized_pnl,
        unrealized_pnl=statistics.unrealized_pnl,
        current_equity=statistics.current_equity,
        peak_equity=statistics.peak_equity,
        current_drawdown=statistics.current_drawdown,
        win_rate=metrics.win_rate,
        total_return=metrics.total_return,
        maximum_drawdown=metrics.maximum_drawdown,
    )


def create_paper_runtime_result_publisher(
    bus: OperationsBus,
    *,
    source: str = "paper-runtime",
) -> PaperRuntimeResultSink:
    """Create a sink that publishes presentation-safe paper-runtime updates."""

    if not isinstance(bus, OperationsBus):
        raise TypeError("bus must be an OperationsBus")

    normalized_source = source.strip()

    if not normalized_source:
        raise ValueError("source must not be empty")

    def publish_result(
        result: PaperRuntimeCycleResult,
    ) -> None:
        bus.publish(
            PaperRuntimeUpdated(
                source=normalized_source,
                snapshot=create_paper_runtime_snapshot(result),
            )
        )

    return publish_result


__all__ = [
    "PaperRuntimeResultSink",
    "create_paper_runtime_result_publisher",
    "create_paper_runtime_snapshot",
]
