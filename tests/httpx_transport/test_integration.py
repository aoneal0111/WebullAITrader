import httpx

from app.composition import CompositionRoot, factory, implements_methods
from app.http_client import HTTPClient, HTTPClientPolicy
from app.http_pipeline import DeterministicHTTPRequestPipeline, PipelinePolicy
from app.httpx_transport import HTTPXTransportAdapter, HTTPXTransportPolicy
from tests.httpx_transport.helpers import PipelineTransportShell, operation


def test_composition_wires_client_pipeline_adapter_and_injected_client():
    calls = []
    injected = httpx.Client(transport=httpx.MockTransport(
        lambda request: calls.append(request) or httpx.Response(200, json={"ok": True})))
    try:
        root = CompositionRoot()
        root.register("httpx_client", factory(lambda: injected, validator=implements_methods("request")))
        root.register("httpx_adapter", factory(
            lambda client: HTTPXTransportAdapter(client, HTTPXTransportPolicy(enabled=True)),
            ("httpx_client",), implements_methods("send")))
        root.register("http_pipeline", factory(
            lambda: DeterministicHTTPRequestPipeline(PipelinePolicy()),
            validator=implements_methods("prepare", "finalize")))
        root.register("pipeline_transport", factory(
            PipelineTransportShell, ("http_pipeline", "httpx_adapter"), implements_methods("send")))
        root.register("http_client", factory(
            lambda transport: HTTPClient(transport, HTTPClientPolicy()),
            ("pipeline_transport",), implements_methods("execute")))
        container = root.build()
        shell = container.resolve("pipeline_transport")
        assert container.resolve("http_client").transport is shell
        assert shell.adapter is container.resolve("httpx_adapter")
        assert calls == []
        result = shell.adapter.send(shell.pipeline.prepare(operation()))
        assert shell.pipeline.finalize(result).body == {"ok": True}
        assert len(calls) == 1
    finally:
        injected.close()
