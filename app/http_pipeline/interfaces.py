from typing import Protocol

from app.http_pipeline.models import HTTPRequestOperation, HTTPResponseOperation


class HTTPRequestPipeline(Protocol):
    def prepare(self, request: HTTPRequestOperation) -> HTTPRequestOperation: ...
    def finalize(self, response: HTTPResponseOperation) -> HTTPResponseOperation: ...
