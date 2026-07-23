"""Composition root for the Paper Order Book application graph."""

from functools import lru_cache

from app.paper_order_book.orchestrator import PaperOrderBookOrchestrator
from app.paper_order_book.runtime import PaperOrderBookRuntime
from app.paper_order_book.service import PaperOrderBookService


def create_runtime() -> PaperOrderBookRuntime:
    return PaperOrderBookRuntime()


def create_orchestrator() -> PaperOrderBookOrchestrator:
    return PaperOrderBookOrchestrator(runtime=create_runtime())


def create_service() -> PaperOrderBookService:
    runtime = PaperOrderBookRuntime()
    orchestrator = PaperOrderBookOrchestrator(runtime=runtime)
    return PaperOrderBookService(orchestrator=orchestrator)


@lru_cache(maxsize=1)
def default_service() -> PaperOrderBookService:
    return create_service()


__all__ = ("create_service", "default_service")
