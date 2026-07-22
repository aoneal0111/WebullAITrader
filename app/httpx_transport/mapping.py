import json

import httpx

from app.committee.models import thaw_json_value
from app.http_pipeline import HTTPRequestOperation, HTTPResponseOperation
from app.httpx_transport.exceptions import HTTPXTransportResponseError
from app.httpx_transport.models import HTTPXRequestArguments
from app.httpx_transport.policies import HTTPXTransportPolicy


def response_identifier(request_id):
    return f"{request_id}:response"


def map_request(request: HTTPRequestOperation, policy: HTTPXTransportPolicy):
    return HTTPXRequestArguments(
        request.method.value, request.url, request.headers, request.query_parameters,
        request.body, request.body is not None, policy.timeout_seconds, policy.follow_redirects)


def request_kwargs(arguments: HTTPXRequestArguments):
    values = {
        "method": arguments.method, "url": arguments.url,
        "headers": arguments.headers, "params": arguments.query_parameters,
        "timeout": float(arguments.timeout_seconds),
        "follow_redirects": arguments.follow_redirects,
    }
    if arguments.has_body:
        values["json"] = arguments.body_value()
    return values


def _response_body(response):
    if not response.content:
        return None
    try:
        return response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return response.text


def map_response(request: HTTPRequestOperation, response: httpx.Response):
    try:
        body = _response_body(response)
        headers = tuple(response.headers.items())
        return HTTPResponseOperation(
            response_identifier(request.request_id), response.status_code, headers,
            thaw_json_value(body), request.context,
            {"deterministic": True, "request_id": request.request_id})
    except HTTPXTransportResponseError:
        raise
    except Exception as exc:
        raise HTTPXTransportResponseError("invalid HTTP response mapping") from exc
