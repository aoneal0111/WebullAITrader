"""Public application service for Paper Order Book coordination."""

from app.paper_order_book.models import (
    PaperOrderBookRequest,
    PaperOrderBookResult,
)
from app.paper_order_book.orchestrator import PaperOrderBookOrchestrator


class PaperOrderBookService:
    """Forward application requests to an injected orchestrator."""

    def __init__(
        self,
        orchestrator: PaperOrderBookOrchestrator | None = None,
    ) -> None:
        self._orchestrator = (
            orchestrator
            if orchestrator is not None
            else PaperOrderBookOrchestrator()
        )

    def execute(
        self,
        request: PaperOrderBookRequest,
    ) -> PaperOrderBookResult:
        return self._orchestrator.execute(request)


__all__ = ("PaperOrderBookService",)
