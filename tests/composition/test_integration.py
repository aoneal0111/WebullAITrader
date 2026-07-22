from datetime import datetime, timezone
from decimal import Decimal

from app.broker_adapter import BrokerAdapter
from app.composition import CompositionRoot, factory, implements_methods
from app.http_client import HTTPClient, HTTPClientPolicy
from app.http_runtime import HTTPRuntime, HTTPRuntimePolicy
from app.transport_runtime import TransportRuntime, TransportRuntimePolicy
from app.webull_transport import WebullTransport, WebullTransportPolicy, WebullTransportState
from tests.composition.helpers import FakeGateway, FakeTransport, GatewayProtocolShell, TransportExecutorShell


def test_complete_graph_is_constructed_without_execution():
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    root = CompositionRoot()
    root.register("raw_transport", factory(FakeTransport))
    root.register("http_client", factory(
        lambda transport: HTTPClient(transport, HTTPClientPolicy(client_enabled=True)),
        ("raw_transport",), implements_methods("execute"),
    ))
    root.register("http_runtime", factory(
        lambda client: HTTPRuntime(client, HTTPRuntimePolicy(runtime_enabled=True)),
        ("http_client",), implements_methods("execute"),
    ))
    root.register("transport_executor", factory(TransportExecutorShell, ("http_runtime",)))
    root.register("transport_runtime", factory(
        lambda executor: TransportRuntime(executor, TransportRuntimePolicy(runtime_enabled=True)),
        ("transport_executor",), implements_methods("execute"),
    ))
    root.register("gateway_protocol", factory(GatewayProtocolShell, ("transport_runtime",), implements_methods("submit_order")))
    root.register("gateway_port", factory(FakeGateway))
    root.register("webull_transport", factory(
        lambda gateway: WebullTransport(
            gateway,
            WebullTransportPolicy(transport_enabled=False, maximum_quantity=1,
                maximum_notional=Decimal("1"), allowed_symbols=("AAPL",), required_environment="production-live"),
            WebullTransportState(timestamp, ()), timestamp,
        ), ("gateway_port",), implements_methods("submit_order"),
    ))
    root.register("broker_adapter", factory(BrokerAdapter, ("webull_transport",), implements_methods("execute")))

    container = root.build()
    adapter = container.resolve("broker_adapter")
    assert adapter.transport is container.resolve("webull_transport")
    assert container.resolve("gateway_protocol").runtime is container.resolve("transport_runtime")
    assert container.list_components()[-1] == "broker_adapter"
