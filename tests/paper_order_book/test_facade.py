import app.paper_order_book.facade as facade
from app.paper_order_book import PaperOrderBookRuntime, execute
from tests.paper_order_book.helpers import make_request


class RecordingService:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute(self, request):
        self.calls.append(request)
        return self.result


def test_execute_delegates_and_returns_exact_service_result(monkeypatch) -> None:
    request = make_request(commands=())
    expected = PaperOrderBookRuntime().evaluate(request)
    service = RecordingService(expected)
    monkeypatch.setattr(facade, "_service", service)

    result = facade.execute(request)

    assert service.calls == [request]
    assert service.calls[0] is request
    assert result is expected


def test_module_singleton_service_is_reused(monkeypatch) -> None:
    request = make_request(commands=())
    expected = PaperOrderBookRuntime().evaluate(request)
    service = RecordingService(expected)
    monkeypatch.setattr(facade, "_service", service)

    facade.execute(request)
    facade.execute(request)

    assert service.calls == [request, request]


def test_repeated_default_facade_execution_is_deterministic() -> None:
    request = make_request(commands=())

    first = facade.execute(request)
    second = facade.execute(request)

    assert first == second
    assert first is not second


def test_facade_introduces_no_mutation() -> None:
    request = make_request(commands=())
    book = request.snapshot.order_book
    before = book.history()

    facade.execute(request)

    assert book.history() == before
    assert book.history()[0] is before[0]


def test_execute_is_exported_from_package() -> None:
    assert execute is facade.execute
