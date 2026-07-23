import pytest

from app.authentication import (
    AuthenticationPolicy, AuthenticationProviderError, validate_authentication_request,
    validate_dependencies,
)
from tests.authentication.helpers import FakeCredentialProvider, FakeVerifier, request


def test_dependencies_and_request_validate():
    assert validate_dependencies(FakeCredentialProvider(), FakeVerifier(), AuthenticationPolicy())
    assert validate_authentication_request(request()) == request()


@pytest.mark.parametrize("provider,verifier,policy", [
    (object(), FakeVerifier(), AuthenticationPolicy()),
    (FakeCredentialProvider(), object(), AuthenticationPolicy()),
    (FakeCredentialProvider(), FakeVerifier(), object()),
])
def test_invalid_dependencies_are_rejected(provider, verifier, policy):
    with pytest.raises(AuthenticationProviderError):
        validate_dependencies(provider, verifier, policy)


def test_invalid_request_type_is_rejected():
    with pytest.raises(AuthenticationProviderError):
        validate_authentication_request(object())
