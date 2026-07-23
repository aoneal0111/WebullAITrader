import pytest

from app.authentication_transport import (
    AuthenticationTransportDependencyError, AuthenticationTransportPolicy,
    validate_dependencies, validate_request,
)
from tests.authentication_transport.helpers import (
    FakeAuthenticationService, FakePipeline, FakeRequestFactory, FakeResponseVerifier,
    FakeTransport, connector_request,
)


def dependencies():
    return (FakeAuthenticationService(), FakeRequestFactory(), FakePipeline(),
            FakeTransport(), FakeResponseVerifier(), AuthenticationTransportPolicy())


def test_dependencies_and_request_validate():
    assert validate_dependencies(*dependencies())
    assert validate_request(connector_request()) == connector_request()


@pytest.mark.parametrize("index", range(6))
def test_invalid_dependencies_are_rejected(index):
    values = list(dependencies())
    values[index] = object()
    with pytest.raises(AuthenticationTransportDependencyError):
        validate_dependencies(*values)


def test_invalid_request_type_is_rejected():
    with pytest.raises(AuthenticationTransportDependencyError):
        validate_request(object())
