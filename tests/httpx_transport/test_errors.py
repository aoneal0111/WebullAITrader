import httpx
import pytest

from app.httpx_transport import (
    HTTPXTransportAdapter, HTTPXTransportConnectionError, HTTPXTransportPolicy,
    HTTPXTransportRequestError, HTTPXTransportResponseError, HTTPXTransportTimeoutError,
)
from tests.httpx_transport.helpers import RecordingClient, operation


@pytest.mark.parametrize("raw,expected", [
    (httpx.ReadTimeout("timeout"), HTTPXTransportTimeoutError),
    (httpx.ConnectError("connection"), HTTPXTransportConnectionError),
    (httpx.InvalidURL("bad URL"), HTTPXTransportRequestError),
])
def test_httpx_errors_are_normalized_with_cause_and_no_retry(raw, expected):
    client = RecordingClient(error=raw)
    with pytest.raises(expected) as captured:
        HTTPXTransportAdapter(client, HTTPXTransportPolicy(enabled=True)).send(operation())
    assert captured.value.__cause__ is raw
    assert len(client.calls) == 1


def test_invalid_response_type_and_body_are_normalized():
    client = RecordingClient(response=object())
    with pytest.raises(HTTPXTransportResponseError):
        HTTPXTransportAdapter(client, HTTPXTransportPolicy(enabled=True)).send(operation())
    response = httpx.Response(200, content=b'{"invalid":NaN}',
                              headers={"content-type": "application/json"})
    with pytest.raises(HTTPXTransportResponseError) as captured:
        HTTPXTransportAdapter(RecordingClient(response), HTTPXTransportPolicy(enabled=True)).send(operation())
    assert captured.value.__cause__ is not None
