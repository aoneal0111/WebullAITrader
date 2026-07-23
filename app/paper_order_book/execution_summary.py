"""Internal immutable summary derived from command execution traces."""

from dataclasses import dataclass

from app.paper_order_book.execution_trace import (
    COMPLETED,
    FAILED,
    PaperOrderBookExecutionTraceEntry,
)


@dataclass(frozen=True, slots=True)
class PaperOrderBookExecutionSummary:
    """Describe aggregate command outcomes for one orchestrator execution."""

    total_commands: int
    completed_commands: int
    failed_commands: int
    outcome: str


def summarize_execution_trace(
    trace: tuple[PaperOrderBookExecutionTraceEntry, ...],
) -> PaperOrderBookExecutionSummary:
    """Derive one immutable summary solely from immutable trace entries."""

    completed_commands = sum(
        entry.outcome == COMPLETED for entry in trace
    )
    failed_commands = sum(entry.outcome == FAILED for entry in trace)
    return PaperOrderBookExecutionSummary(
        total_commands=len(trace),
        completed_commands=completed_commands,
        failed_commands=failed_commands,
        outcome=FAILED if failed_commands else COMPLETED,
    )


__all__ = ()
