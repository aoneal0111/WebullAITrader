from app.http_pipeline import HTTPRequestSerializer, PipelinePolicy, normalize_body, normalize_headers, normalize_query
from tests.http_pipeline.helpers import request


def test_header_and_query_normalization_is_stable():
    assert normalize_headers((("Z", " 2 "), ("A", " 1 "))) == (("a", "1"), ("z", "2"))
    assert normalize_query((("z", "2"), ("a", "1"))) == (("a", "1"), ("z", "2"))


def test_body_normalization_returns_plain_json_structure():
    value = request().body
    assert normalize_body(value) == {"items": [2, 1]}


def test_serializer_preserves_identifiers_context_and_input():
    original = request()
    prepared = HTTPRequestSerializer().serialize(original, PipelinePolicy())
    assert prepared.request_id == original.request_id
    assert prepared.context is original.context
    assert prepared.headers == (("content-type", "application/json"), ("x-zeta", "z"))
    assert prepared.query_parameters == (("a", "1"), ("z", "2"))
    assert original.headers[0][0] == "X-Zeta"


def test_normalization_can_be_disabled_explicitly():
    original = request()
    prepared = HTTPRequestSerializer().serialize(
        original, PipelinePolicy(normalize_headers=False, normalize_query_order=False))
    assert prepared.headers == original.headers
    assert prepared.query_parameters == original.query_parameters
