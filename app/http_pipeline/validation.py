from app.http_pipeline.exceptions import InvalidPipelineRequestError, InvalidPipelineResponseError
from app.http_pipeline.models import HTTPRequestOperation, HTTPResponseOperation


def _duplicate_header(headers):
    names = [name.casefold() for name, _ in headers]
    return len(names) != len(set(names))


def validate_request(request):
    if not isinstance(request, HTTPRequestOperation):
        raise InvalidPipelineRequestError("request must be HTTPRequestOperation")
    if _duplicate_header(request.headers):
        raise InvalidPipelineRequestError("duplicate header names are not allowed")
    return request


def validate_response(response):
    if not isinstance(response, HTTPResponseOperation):
        raise InvalidPipelineResponseError("response must be HTTPResponseOperation")
    if _duplicate_header(response.headers):
        raise InvalidPipelineResponseError("duplicate header names are not allowed")
    return response
