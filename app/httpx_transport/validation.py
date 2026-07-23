import httpx

from app.http_pipeline import HTTPRequestOperation
from app.http_runtime import HTTPMethod
from app.httpx_transport.exceptions import HTTPXTransportRequestError
from app.httpx_transport.policies import HTTPXTransportPolicy


def validate_dependencies(client, policy):
    if not callable(getattr(client, "request", None)):
        raise HTTPXTransportRequestError("client must provide a synchronous request operation")
    if not isinstance(policy, HTTPXTransportPolicy):
        raise HTTPXTransportRequestError("policy must be HTTPXTransportPolicy")
    return True


def validate_request(request):
    if not isinstance(request, HTTPRequestOperation):
        raise HTTPXTransportRequestError("request must be HTTPRequestOperation")
    if request.method not in tuple(HTTPMethod):
        raise HTTPXTransportRequestError("unsupported HTTP method")
    if not request.url:
        raise HTTPXTransportRequestError("request URL must be non-empty")
    return request


def validate_raw_response(response, verify_type=True):
    if not isinstance(verify_type, bool):
        raise HTTPXTransportRequestError("verify_type must be boolean")
    if verify_type and not isinstance(response, httpx.Response):
        from app.httpx_transport.exceptions import HTTPXTransportResponseError
        raise HTTPXTransportResponseError("client returned an invalid response type")
    return response
