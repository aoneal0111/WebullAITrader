import app.paper_order_book.facade as facade
from app.paper_order_book import (
    PaperOrderBookOrchestrator,
    PaperOrderBookRuntime,
    PaperOrderBookService,
    create_service,
    default_service,
)
from tests.paper_order_book.helpers import make_request


def test_create_service_builds_complete_application_graph() -> None:
    service = create_service()

    assert isinstance(service, PaperOrderBookService)
    assert isinstance(service._orchestrator, PaperOrderBookOrchestrator)
    assert isinstance(service._orchestrator._runtime, PaperOrderBookRuntime)


def test_create_service_returns_distinct_graphs() -> None:
    first = create_service()
    second = create_service()

    assert first is not second
    assert first._orchestrator is not second._orchestrator
    assert first._orchestrator._runtime is not second._orchestrator._runtime


def test_default_service_returns_one_cached_singleton() -> None:
    assert default_service() is default_service()


def test_facade_uses_composed_default_service() -> None:
    assert facade._service is default_service()
    request = make_request(commands=())

    result = facade.execute(request)

    assert result.identity is request.identity
    assert result.snapshot.order_book is request.snapshot.order_book
