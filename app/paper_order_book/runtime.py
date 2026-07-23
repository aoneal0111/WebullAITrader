"""Pure observational runtime for Paper Order Book coordination."""

from app.paper_order_book.models import (
    PaperOrderBookRequest,
    PaperOrderBookResult,
    PaperOrderBookSummary,
)
from app.paper_order_book.validation import validate_request


class PaperOrderBookRuntime:
    """Evaluate application structure without changing lifecycle state."""

    def evaluate(
        self,
        request: PaperOrderBookRequest,
    ) -> PaperOrderBookResult:
        criteria = validate_request(request)
        summary = PaperOrderBookSummary(
            initial_orders=len(request.snapshot.order_book),
            command_count=len(request.commands),
        )
        return PaperOrderBookResult(
            identity=request.identity,
            snapshot=request.snapshot,
            commands=request.commands,
            summary=summary,
            criteria=criteria,
            requested_at=request.requested_at,
            completed_at=request.completed_at,
            errors=criteria.errors,
        )


__all__ = ("PaperOrderBookRuntime",)
