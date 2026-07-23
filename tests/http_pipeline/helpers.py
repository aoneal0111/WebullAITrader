from app.http_pipeline import HTTPRequestOperation, HTTPResponseOperation, PipelineContext
from app.http_runtime import HTTPMethod


def context():
    return PipelineContext("correlation-1")


def request(**changes):
    values = dict(request_id="request-1", method=HTTPMethod.POST,
                  url="https://example.invalid/resource",
                  headers=(("X-Zeta", " z "), ("Content-Type", " application/json ")),
                  query_parameters=(("z", "2"), ("a", "1")),
                  body={"items": [2, 1]}, context=context())
    values.update(changes)
    return HTTPRequestOperation(**values)


def response(**changes):
    values = dict(response_id="response-1", status_code=200,
                  headers=(("X-Zeta", " z "), ("Content-Type", " application/json ")),
                  body={"ok": True}, context=context())
    values.update(changes)
    return HTTPResponseOperation(**values)


class FakeTransport:
    def __init__(self):
        self.requests = []

    def send(self, serialized_request):
        self.requests.append(serialized_request)
        raise AssertionError("composition must not execute transport")


class PipelineTransportShell:
    def __init__(self, pipeline, transport):
        self.pipeline = pipeline
        self.transport = transport

    def send(self, request):
        raise AssertionError("composition must not execute pipeline or transport")
