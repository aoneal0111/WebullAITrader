import httpx

from app.http_pipeline import HTTPRequestOperation, PipelineContext
from app.http_runtime import HTTPMethod


def operation(**changes):
    values = dict(request_id="request-1", method=HTTPMethod.POST,
                  url="https://mock.invalid/resource",
                  headers=(("content-type", "application/json"), ("x-order", "one")),
                  query_parameters=(("a", "1"), ("z", "2")),
                  body={"value": 7}, context=PipelineContext("correlation-1"))
    values.update(changes)
    return HTTPRequestOperation(**values)


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


class RecordingClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class PipelineTransportShell:
    def __init__(self, pipeline, adapter):
        self.pipeline = pipeline
        self.adapter = adapter

    def send(self, request):
        raise AssertionError("composition must not execute the transport graph")
