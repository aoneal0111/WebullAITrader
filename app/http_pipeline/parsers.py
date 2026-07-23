from app.committee.models import thaw_json_value
from app.http_pipeline.exceptions import InvalidPipelineResponseError
from app.http_pipeline.models import HTTPResponseOperation
from app.http_pipeline.policies import PipelinePolicy
from app.http_pipeline.serializers import normalize_headers


class HTTPResponseParser:
    def parse(self, response, policy):
        if not isinstance(response, HTTPResponseOperation):
            raise InvalidPipelineResponseError("response must be HTTPResponseOperation")
        if not isinstance(policy, PipelinePolicy):
            raise InvalidPipelineResponseError("policy must be PipelinePolicy")
        return HTTPResponseOperation(
            response.response_id, response.status_code,
            normalize_headers(response.headers, policy.normalize_headers),
            thaw_json_value(response.body), response.context, response.metadata)
