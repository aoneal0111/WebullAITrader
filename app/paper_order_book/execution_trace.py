"""Internal immutable diagnostics for command execution."""

from dataclasses import dataclass
from datetime import datetime

from app.paper_order_book.models import PaperOrderBookCommand

COMPLETED = "completed"
DISPATCHED = "dispatched"
FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PaperOrderBookExecutionTraceEntry:
    """Describe one command dispatch attempt without retaining its payload."""

    command_type: str
    stage: str
    outcome: str
    occurred_at: datetime


def trace_command_dispatch(
    command: PaperOrderBookCommand,
    outcome: str,
) -> PaperOrderBookExecutionTraceEntry:
    """Create a diagnostic entry without inspecting or copying its payload."""

    return PaperOrderBookExecutionTraceEntry(
        command_type=command.command_type,
        stage=DISPATCHED,
        outcome=outcome,
        occurred_at=command.occurred_at,
    )


__all__ = ()
