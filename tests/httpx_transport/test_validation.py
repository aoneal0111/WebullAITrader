import httpx
import pytest

from app.httpx_transport import (
    HTTPXTransportPolicy, HTTPXTransportRequestError, HTTPXTransportResponseError,
    validate_dependencies, validate_raw_response, validate_request,
)
from tests.httpx_transport.helpers import RecordingClient, operation


def test_dependencies_request_and_response_validate():
    assert validate_dependencies(RecordingClient(), HTTPXTransportPolicy())
    assert validate_request(operation()) == operation()
    response = httpx.Response(200)
    assert validate_raw_response(response) is response


@pytest.mark.parametrize("client,policy", [
    (object(), HTTPXTransportPolicy()), (RecordingClient(), object()),
])
def test_invalid_dependencies_use_domain_error(client, policy):
    with pytest.raises(HTTPXTransportRequestError):
        validate_dependencies(client, policy)


def test_invalid_request_and_response_types_use_domain_errors():
    with pytest.raises(HTTPXTransportRequestError):
        validate_request(object())
    with pytest.raises(HTTPXTransportResponseError):
        validate_raw_response(object())
