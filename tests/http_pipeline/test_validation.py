import pytest

from app.http_pipeline import (
    InvalidPipelineRequestError, InvalidPipelineResponseError, validate_request, validate_response,
)
from tests.http_pipeline.helpers import request, response


def test_valid_models_pass_validation():
    assert validate_request(request()) == request()
    assert validate_response(response()) == response()


def test_duplicate_headers_are_case_insensitive():
    with pytest.raises(InvalidPipelineRequestError, match="duplicate"):
        validate_request(request(headers=(("X-Test", "one"), ("x-test", "two"))))
    with pytest.raises(InvalidPipelineResponseError, match="duplicate"):
        validate_response(response(headers=(("X-Test", "one"), ("x-test", "two"))))


def test_invalid_types_are_normalized():
    with pytest.raises(InvalidPipelineRequestError):
        validate_request(object())
    with pytest.raises(InvalidPipelineResponseError):
        validate_response(object())
