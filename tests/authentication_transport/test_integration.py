import httpx
import pytest

from app.authentication import (
    AuthenticationPolicy, AuthenticationStatus, DeterministicAuthenticationService,
)
from app.authentication_transport import (
    AuthenticationRequestExecutionError, AuthenticationTransportPolicy,
    DeterministicAuthenticationTransportConnector,
)
from app.composition import CompositionRoot, factory, implements_methods
from app.http_pipeline import DeterministicHTTPRequestPipeline, PipelinePolicy
from app.httpx_transport import HTTPXTransportAdapter, HTTPXTransportPolicy
from tests.authentication.helpers import FakeCredentialProvider, FakeVerifier
from tests.authentication_transport.helpers import (
    FakeRequestFactory, FakeResponseVerifier, connector_request,
)


def graph(handler):
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: calls.append(request) or handler(request)))
    root = CompositionRoot()
    root.register("credential_provider", factory(FakeCredentialProvider))
    root.register("authentication_verifier", factory(FakeVerifier))
    root.register("authentication_service", factory(
        lambda provider, verifier: DeterministicAuthenticationService(
            provider, verifier, AuthenticationPolicy()),
        ("credential_provider", "authentication_verifier")))
    root.register("request_factory", factory(FakeRequestFactory))
    root.register("pipeline", factory(lambda: DeterministicHTTPRequestPipeline(PipelinePolicy())))
    root.register("httpx_client", factory(lambda: client))
    root.register("transport", factory(
        lambda injected: HTTPXTransportAdapter(injected, HTTPXTransportPolicy(enabled=True)),
        ("httpx_client",)))
    root.register("response_verifier", factory(FakeResponseVerifier))
    root.register("connector", factory(
        lambda service, request_factory, pipeline, transport, verifier:
            DeterministicAuthenticationTransportConnector(
                service, request_factory, pipeline, transport, verifier,
                AuthenticationTransportPolicy(enabled=True)),
        ("authentication_service", "request_factory", "pipeline", "transport", "response_verifier"),
        implements_methods("authenticate")))
    return root, client, calls


def test_construction_only_has_no_authentication_or_transport_execution():
    root, client, calls = graph(lambda request: httpx.Response(200, json={"accepted": True}))
    try:
        container = root.build()
        assert container.resolve("connector") is not None
        assert calls == []
        assert container.resolve("authentication_service").state().status is AuthenticationStatus.UNAUTHENTICATED
    finally:
        client.close()


def test_deterministic_mock_transport_execution():
    root, client, calls = graph(lambda request: httpx.Response(200, json={"accepted": True}))
    try:
        connector = root.build().resolve("connector")
        result = connector.authenticate(connector_request())
        assert result.success and len(calls) == 1
    finally:
        client.close()


def test_mock_transport_failure_leaves_service_unauthenticated():
    def timeout(request):
        raise httpx.ReadTimeout("timeout", request=request)
    root, client, calls = graph(timeout)
    try:
        container = root.build()
        with pytest.raises(AuthenticationRequestExecutionError):
            container.resolve("connector").authenticate(connector_request())
        assert len(calls) == 1
        assert container.resolve("authentication_service").state().status is AuthenticationStatus.UNAUTHENTICATED
    finally:
        client.close()
