from app.paper_order_book import (
    PaperOrderBookOrchestrator,
    PaperOrderBookRuntime,
    PaperOrderBookService,
)
from tests.paper_order_book.helpers import make_request


class RecordingOrchestrator:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute(self, request):
        self.calls.append(request)
        return self.result


def test_execute_delegates_and_returns_exact_result() -> None:
    request = make_request(commands=())
    expected = PaperOrderBookRuntime().evaluate(request)
    orchestrator = RecordingOrchestrator(expected)
    service = PaperOrderBookService(orchestrator)

    result = service.execute(request)

    assert orchestrator.calls == [request]
    assert orchestrator.calls[0] is request
    assert result is expected


def test_constructor_preserves_injected_orchestrator() -> None:
    request = make_request(commands=())
    expected = PaperOrderBookRuntime().evaluate(request)
    orchestrator = RecordingOrchestrator(expected)

    service = PaperOrderBookService(orchestrator)

    assert service._orchestrator is orchestrator


def test_default_constructor_creates_application_orchestrator() -> None:
    service = PaperOrderBookService()
    assert isinstance(service._orchestrator, PaperOrderBookOrchestrator)


def test_default_service_is_deterministic_for_repeated_empty_execution() -> None:
    request = make_request(commands=())
    service = PaperOrderBookService()

    first = service.execute(request)
    second = service.execute(request)

    assert first == second
    assert first is not second


def test_service_itself_does_not_mutate_caller_owned_state() -> None:
    request = make_request(commands=())
    book = request.snapshot.order_book
    before = book.history()
    expected = PaperOrderBookRuntime().evaluate(request)
    service = PaperOrderBookService(RecordingOrchestrator(expected))

    service.execute(request)

    assert book.history() == before
    assert book.history()[0] is before[0]
