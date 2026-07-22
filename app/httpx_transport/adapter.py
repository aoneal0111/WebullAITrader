import httpx

from app.httpx_transport.exceptions import (
    HTTPXTransportConnectionError, HTTPXTransportDisabledError,
    HTTPXTransportRequestError, HTTPXTransportResponseError, HTTPXTransportTimeoutError,
)
from app.httpx_transport.mapping import map_request, map_response, request_kwargs
from app.httpx_transport.policies import HTTPXTransportPolicy
from app.httpx_transport.validation import validate_dependencies, validate_raw_response, validate_request


class HTTPXTransportAdapter:
    def __init__(self, client, policy: HTTPXTransportPolicy):
        validate_dependencies(client, policy)
        self._client = client
        self._policy = policy

    def send(self, request):
        request = validate_request(request)
        if not self._policy.enabled:
            raise HTTPXTransportDisabledError("httpx transport is disabled")
        arguments = map_request(request, self._policy)
        try:
            response = self._client.request(**request_kwargs(arguments))
        except httpx.TimeoutException as exc:
            raise HTTPXTransportTimeoutError("HTTP request timed out") from exc
        except httpx.ConnectError as exc:
            raise HTTPXTransportConnectionError("HTTP connection failed") from exc
        except (httpx.InvalidURL, httpx.UnsupportedProtocol, httpx.RequestError) as exc:
            raise HTTPXTransportRequestError("HTTP request failed") from exc
        except httpx.HTTPError as exc:
            raise HTTPXTransportRequestError("HTTP transport failed") from exc
        try:
            validate_raw_response(response, self._policy.verify_response_type)
            return map_response(request, response)
        except HTTPXTransportResponseError:
            raise
        except Exception as exc:
            raise HTTPXTransportResponseError("HTTP response validation failed") from exc
