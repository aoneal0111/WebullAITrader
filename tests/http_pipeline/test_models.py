from dataclasses import FrozenInstanceError
import pytest

from app.http_pipeline import HTTPRequestOperation, HTTPResponseOperation, PipelineContext
from tests.http_pipeline.helpers import request, response


def test_models_are_frozen_slotted_immutable_and_round_trip():
    context = PipelineContext("correlation-1", {"caller": "outer"})
    request_value = request(context=context)
    response_value = response(context=context)
    assert PipelineContext.from_dict(context.to_dict()) == context
    assert HTTPRequestOperation.from_dict(request_value.to_dict()) == request_value
    assert HTTPResponseOperation.from_dict(response_value.to_dict()) == response_value
    assert not hasattr(request_value, "__dict__")
    with pytest.raises(FrozenInstanceError):
        request_value.url = "changed"
    with pytest.raises(TypeError):
        request_value.body["new"] = True


@pytest.mark.parametrize("changes", [
    {"request_id": ""}, {"url": ""}, {"headers": {"x": "y"}},
    {"query_parameters": (("x", ""),)}, {"body": object()},
])
def test_malformed_requests_are_rejected(changes):
    with pytest.raises(ValueError):
        request(**changes)


@pytest.mark.parametrize("changes", [
    {"response_id": ""}, {"status_code": 99}, {"status_code": 600},
    {"headers": {"x": "y"}}, {"body": object()},
])
def test_malformed_responses_are_rejected(changes):
    with pytest.raises(ValueError):
        response(**changes)
