from typing import Protocol

from app.http_pipeline import HTTPRequestOperation, HTTPResponseOperation


class HTTPXTransport(Protocol):
    def send(self, request: HTTPRequestOperation) -> HTTPResponseOperation: ...
