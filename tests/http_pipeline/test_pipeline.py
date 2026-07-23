import pytest

from app.http_pipeline import (
    DeterministicHTTPRequestPipeline, InvalidPipelineRequestError,
    InvalidPipelineResponseError, PipelinePolicy,
)
from tests.http_pipeline.helpers import request, response


def test_prepare_and_finalize_are_deterministic_and_side_effect_free():
    pipeline = DeterministicHTTPRequestPipeline(PipelinePolicy())
    source_request, source_response = request(), response()
    first_request = pipeline.prepare(source_request)
    first_response = pipeline.finalize(source_response)
    assert pipeline.prepare(source_request) == first_request
    assert pipeline.finalize(source_response) == first_response
    assert source_request.headers[0][0] == "X-Zeta"
    assert source_response.headers[0][0] == "X-Zeta"


def test_pipeline_rejects_malformed_types_and_duplicates_before_translation():
    pipeline = DeterministicHTTPRequestPipeline(PipelinePolicy())
    with pytest.raises(InvalidPipelineRequestError):
        pipeline.prepare(object())
    with pytest.raises(InvalidPipelineResponseError):
        pipeline.finalize(object())
    with pytest.raises(InvalidPipelineRequestError, match="duplicate"):
        pipeline.prepare(request(headers=(("X", "1"), ("x", "2"))))
