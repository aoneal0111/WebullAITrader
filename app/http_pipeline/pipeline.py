from app.http_pipeline.parsers import HTTPResponseParser
from app.http_pipeline.policies import PipelinePolicy
from app.http_pipeline.serializers import HTTPRequestSerializer
from app.http_pipeline.validation import validate_request, validate_response


class DeterministicHTTPRequestPipeline:
    def __init__(self, policy: PipelinePolicy):
        if not isinstance(policy, PipelinePolicy):
            raise ValueError("policy must be PipelinePolicy")
        self._policy = policy
        self._serializer = HTTPRequestSerializer()
        self._parser = HTTPResponseParser()

    def prepare(self, request):
        request = validate_request(request)
        prepared = self._serializer.serialize(request, self._policy)
        return validate_request(prepared)

    def finalize(self, response):
        response = validate_response(response)
        finalized = self._parser.parse(response, self._policy)
        return validate_response(finalized)
