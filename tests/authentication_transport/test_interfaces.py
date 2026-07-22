from app.authentication_transport import (
    AuthenticationRequestFactory, AuthenticationResponseVerifier, AuthenticationTransportConnector,
    DeterministicAuthenticationTransportConnector,
)


def test_interfaces_expose_exact_operations():
    assert {name for name in AuthenticationTransportConnector.__dict__ if not name.startswith("_")} == {"authenticate"}
    assert {name for name in AuthenticationRequestFactory.__dict__ if not name.startswith("_")} == {"create"}
    assert {name for name in AuthenticationResponseVerifier.__dict__ if not name.startswith("_")} == {"verify"}
    assert {name for name in DeterministicAuthenticationTransportConnector.__dict__ if not name.startswith("_")} == {"authenticate"}
