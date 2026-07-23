from app.httpx_transport import HTTPXTransport, HTTPXTransportAdapter


def test_transport_protocol_and_adapter_expose_only_send():
    protocol_public = {name for name in HTTPXTransport.__dict__ if not name.startswith("_")}
    adapter_public = {name for name in HTTPXTransportAdapter.__dict__ if not name.startswith("_")}
    assert protocol_public == {"send"}
    assert adapter_public == {"send"}
