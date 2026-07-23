from decimal import Decimal
import httpx
import pytest

from app.httpx_transport import (
    HTTPXTransportAdapter, HTTPXTransportDisabledError, HTTPXTransportPolicy,
)
from tests.httpx_transport.helpers import RecordingClient, client, operation


def test_disabled_by_default_does_not_invoke_client():
    injected = RecordingClient()
    adapter = HTTPXTransportAdapter(injected, HTTPXTransportPolicy())
    with pytest.raises(HTTPXTransportDisabledError):
        adapter.send(operation())
    assert injected.calls == []


def test_mock_transport_receives_exact_method_url_headers_query_json_once():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(201, headers={"x-result": "ok"}, json={"accepted": True})
    injected = client(handler)
    try:
        source = operation()
        result = HTTPXTransportAdapter(
            injected, HTTPXTransportPolicy(enabled=True, timeout_seconds=Decimal("2"))).send(source)
    finally:
        injected.close()
    assert len(seen) == 1
    raw = seen[0]
    assert raw.method == "POST"
    assert str(raw.url) == "https://mock.invalid/resource?a=1&z=2"
    assert raw.headers["x-order"] == "one"
    assert raw.read() == b'{"value":7}'
    assert result.status_code == 201 and result.headers == (("x-result", "ok"), ("content-length", "17"), ("content-type", "application/json"))
    assert result.context is source.context


def test_equivalent_requests_produce_equivalent_results_without_mutation():
    injected = client(lambda request: httpx.Response(200, json={"ok": True}))
    try:
        adapter = HTTPXTransportAdapter(injected, HTTPXTransportPolicy(enabled=True))
        source = operation()
        first, second = adapter.send(source), adapter.send(operation())
    finally:
        injected.close()
    assert first == second
    assert source.headers[0] == ("content-type", "application/json")


def test_timeout_and_redirect_arguments_reach_injected_client():
    injected = RecordingClient(httpx.Response(204))
    policy = HTTPXTransportPolicy(enabled=True, timeout_seconds=Decimal("4.25"), follow_redirects=True)
    HTTPXTransportAdapter(injected, policy).send(operation(body=None))
    assert injected.calls[0]["timeout"] == 4.25
    assert injected.calls[0]["follow_redirects"] is True
    assert "json" not in injected.calls[0]
