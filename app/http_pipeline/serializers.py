from app.committee.models import thaw_json_value
from app.http_pipeline.exceptions import SerializationError
from app.http_pipeline.models import HTTPRequestOperation
from app.http_pipeline.policies import PipelinePolicy


def normalize_headers(headers, enabled=True):
    if not isinstance(enabled, bool):
        raise SerializationError("header normalization flag must be boolean")
    if not enabled:
        return headers
    return tuple(sorted(((name.casefold(), value.strip()) for name, value in headers), key=lambda item: item[0]))


def normalize_query(parameters, enabled=True):
    if not isinstance(enabled, bool):
        raise SerializationError("query normalization flag must be boolean")
    return tuple(sorted(parameters, key=lambda item: (item[0], item[1]))) if enabled else parameters


def normalize_body(body):
    try:
        return thaw_json_value(body)
    except Exception as exc:
        raise SerializationError("request body is not JSON-compatible") from exc


class HTTPRequestSerializer:
    def serialize(self, request, policy):
        if not isinstance(request, HTTPRequestOperation):
            raise SerializationError("request must be HTTPRequestOperation")
        if not isinstance(policy, PipelinePolicy):
            raise SerializationError("policy must be PipelinePolicy")
        return HTTPRequestOperation(
            request.request_id, request.method, request.url,
            normalize_headers(request.headers, policy.normalize_headers),
            normalize_query(request.query_parameters, policy.normalize_query_order),
            normalize_body(request.body), request.context, request.metadata)
