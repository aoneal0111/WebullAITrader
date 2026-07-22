from decimal import Decimal
import httpx

from app.http_pipeline import PipelineContext
from app.httpx_transport import (
    HTTPXTransportPolicy, map_request, map_response, request_kwargs, response_identifier,
)
from tests.httpx_transport.helpers import operation


def test_request_mapping_preserves_order_and_values():
    source = operation()
    arguments = map_request(source, HTTPXTransportPolicy(enabled=True, timeout_seconds=Decimal("3.5")))
    kwargs = request_kwargs(arguments)
    assert kwargs == {
        "method": "POST", "url": "https://mock.invalid/resource",
        "headers": source.headers, "params": source.query_parameters,
        "timeout": 3.5, "follow_redirects": False, "json": {"value": 7},
    }
    assert source.body == {"value": 7}


def test_no_body_omits_json_argument_and_redirect_is_propagated():
    arguments = map_request(operation(body=None), HTTPXTransportPolicy(enabled=True, follow_redirects=True))
    kwargs = request_kwargs(arguments)
    assert "json" not in kwargs
    assert kwargs["follow_redirects"] is True


def test_response_mapping_json_text_context_and_identifier():
    source = operation(context=PipelineContext("correlation-x"))
    json_response = httpx.Response(201, headers=(("x-one", "1"),), json={"ok": True})
    mapped = map_response(source, json_response)
    assert mapped.response_id == response_identifier(source.request_id) == "request-1:response"
    assert mapped.body == {"ok": True} and mapped.context is source.context
    text = map_response(source, httpx.Response(202, text="plain response"))
    assert text.body == "plain response"
