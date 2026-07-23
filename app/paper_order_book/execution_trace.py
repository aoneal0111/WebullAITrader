"""Internal immutable diagnostics for command execution."""

from dataclasses import dataclass
from datetime import datetime

from app.paper_order_book.models import PaperOrderBookCommand

DISPATCHED = "dispatched"


@dataclass(frozen=True, slots=True)
class PaperOrderBookExecutionTraceEntry:
    """Describe one command successfully dispatched by the application."""

    command_type: str
    stage: str
    occurred_at: datetime


def trace_dispatched_command(
    command: PaperOrderBookCommand,
) -> PaperOrderBookExecutionTraceEntry:
    """Create a diagnostic entry without inspecting or copying its payload."""

    return PaperOrderBookExecutionTraceEntry(
        command_type=command.command_type,
        stage=DISPATCHED,
        occurred_at=command.occurred_at,
    )


__all__ = ()
