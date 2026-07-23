from app.composition import CompositionRoot, factory, implements_methods
from app.http_client import HTTPClient, HTTPClientPolicy
from app.http_pipeline import DeterministicHTTPRequestPipeline, PipelinePolicy
from tests.http_pipeline.helpers import FakeTransport, PipelineTransportShell


def test_composition_constructs_client_pipeline_transport_without_execution():
    transport = FakeTransport()
    root = CompositionRoot()
    root.register("http_transport", factory(lambda: transport, validator=implements_methods("send")))
    root.register("http_pipeline", factory(
        lambda: DeterministicHTTPRequestPipeline(PipelinePolicy()),
        validator=implements_methods("prepare", "finalize")))
    root.register("pipeline_transport", factory(
        PipelineTransportShell, ("http_pipeline", "http_transport"), implements_methods("send")))
    root.register("http_client", factory(
        lambda boundary: HTTPClient(boundary, HTTPClientPolicy(client_enabled=False)),
        ("pipeline_transport",), implements_methods("execute")))
    container = root.build()
    shell = container.resolve("pipeline_transport")
    assert container.resolve("http_client").transport is shell
    assert shell.pipeline is container.resolve("http_pipeline")
    assert shell.transport is transport
    assert transport.requests == []
