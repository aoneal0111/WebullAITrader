import pytest

from app.credentials import (
    CredentialPolicy, CredentialProviderError, CredentialRequest,
    InvalidCredentialResponseError, ValidatingCredentialProvider,
)
from tests.credentials.helpers import FakeCredentialProvider, response


def test_provider_is_disabled_by_default_without_calling_delegate():
    fake = FakeCredentialProvider(response())
    provider = ValidatingCredentialProvider(fake, CredentialPolicy())
    with pytest.raises(CredentialProviderError, match="disabled"):
        provider.provide(CredentialRequest("broker", "order-entry", ("user",)))
    assert fake.requests == []


def test_enabled_provider_calls_once_and_does_not_cache():
    fake = FakeCredentialProvider(response())
    provider = ValidatingCredentialProvider(fake, CredentialPolicy(provider_enabled=True))
    request = CredentialRequest("broker", "order-entry", ("user",))
    assert provider.provide(request) == response()
    assert provider.provide(request) == response()
    assert fake.requests == [request, request]


def test_invalid_output_and_delegate_error_are_normalized():
    request = CredentialRequest("broker", "order-entry", ("user",))
    with pytest.raises(InvalidCredentialResponseError):
        ValidatingCredentialProvider(FakeCredentialProvider(object()), CredentialPolicy(provider_enabled=True)).provide(request)
    with pytest.raises(CredentialProviderError, match="provider failed"):
        ValidatingCredentialProvider(FakeCredentialProvider(error=LookupError()), CredentialPolicy(provider_enabled=True)).provide(request)


def test_constructor_requires_contract_and_policy():
    with pytest.raises(CredentialProviderError):
        ValidatingCredentialProvider(object(), CredentialPolicy())
    with pytest.raises(CredentialProviderError):
        ValidatingCredentialProvider(FakeCredentialProvider(), object())
