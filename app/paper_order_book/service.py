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
        if orchestrator is None:
            from app.paper_order_book.composition import create_orchestrator

            orchestrator = create_orchestrator()
        self._orchestrator = orchestrator

    def execute(
        self,
        request: PaperOrderBookRequest,
    ) -> PaperOrderBookResult:
        return self._orchestrator.execute(request)


__all__ = ("PaperOrderBookService",)
