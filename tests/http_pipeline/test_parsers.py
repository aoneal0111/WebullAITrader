import pytest

from app.http_pipeline import HTTPResponseParser, InvalidPipelineResponseError, PipelinePolicy
from tests.http_pipeline.helpers import response


def test_response_parser_normalizes_and_preserves_context():
    original = response()
    result = HTTPResponseParser().parse(original, PipelinePolicy())
    assert result.headers == (("content-type", "application/json"), ("x-zeta", "z"))
    assert result.context is original.context
    assert result.body == {"ok": True}


def test_response_parser_rejects_invalid_type():
    with pytest.raises(InvalidPipelineResponseError):
        HTTPResponseParser().parse(object(), PipelinePolicy())
