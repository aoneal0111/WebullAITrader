"""Application orchestrator for public paper-order lifecycle operations."""

import app.paper_order_book.dispatcher as command_dispatcher

from app.paper_order_book.execution_trace import (
    PaperOrderBookExecutionTraceEntry,
    trace_dispatched_command,
)
from app.paper_order_book.models import (
    PaperOrderBookObservation,
    PaperOrderBookRequest,
    PaperOrderBookResult,
    PaperOrderBookSummary,
)
from app.paper_order_book.runtime import PaperOrderBookRuntime


class PaperOrderBookOrchestrator:
    """Validate first, then delegate mutations to the lifecycle facade."""

    def __init__(self, runtime: PaperOrderBookRuntime | None = None) -> None:
        if runtime is None:
            from app.paper_order_book.composition import create_runtime

            runtime = create_runtime()
        self._runtime = runtime
        self._execution_trace: tuple[
            PaperOrderBookExecutionTraceEntry, ...
        ] = ()

    def execute(
        self,
        request: PaperOrderBookRequest,
    ) -> PaperOrderBookResult:
        self._execution_trace = ()
        evaluated = self._runtime.evaluate(request)
        if not evaluated.criteria.accepted:
            return evaluated

        order_book = request.snapshot.order_book
        for command in request.commands:
            command_dispatcher.dispatch_command(order_book, command)
            self._execution_trace = (
                *self._execution_trace,
                trace_dispatched_command(command),
            )

        observation = PaperOrderBookObservation(
            identity=request.snapshot.identity,
            order_book=order_book,
            captured_at=request.completed_at,
        )
        summary = PaperOrderBookSummary(
            initial_orders=len(order_book),
            command_count=len(request.commands),
        )
        return PaperOrderBookResult(
            identity=request.identity,
            snapshot=observation,
            commands=request.commands,
            summary=summary,
            criteria=evaluated.criteria,
            requested_at=request.requested_at,
            completed_at=request.completed_at,
            errors=evaluated.errors,
        )

__all__ = ("PaperOrderBookOrchestrator",)
