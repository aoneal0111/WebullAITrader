import pytest

from app.authentication import (
    AuthenticationFailedError, AuthenticationPolicy, AuthenticationProviderError,
    AuthenticationStatus, DeterministicAuthenticationService, InvalidAuthenticationStateError,
)
from tests.authentication.helpers import FakeCredentialProvider, FakeVerifier, request


def service(provider=None, verifier=None, **policy):
    return DeterministicAuthenticationService(
        provider or FakeCredentialProvider(), verifier or FakeVerifier(), AuthenticationPolicy(**policy))


def test_authenticate_and_logout_state_sequence():
    provider, verifier = FakeCredentialProvider(), FakeVerifier()
    subject = service(provider, verifier)
    result = subject.authenticate(request())
    assert result.success and result.state.status is AuthenticationStatus.AUTHENTICATED
    assert result.state.transition_number == 2
    assert len(provider.requests) == len(verifier.calls) == 1
    assert subject.logout().status is AuthenticationStatus.LOGGED_OUT


def test_failed_verification_resets_state_and_is_repeatable():
    subject = service(verifier=FakeVerifier(False))
    with pytest.raises(AuthenticationFailedError):
        subject.authenticate(request())
    assert subject.state().status is AuthenticationStatus.UNAUTHENTICATED
    with pytest.raises(AuthenticationFailedError):
        subject.authenticate(request())
    assert subject.state().transition_number == 4


def test_provider_and_verifier_errors_are_normalized_and_reset_state():
    subject = service(provider=FakeCredentialProvider(error=LookupError()))
    with pytest.raises(AuthenticationProviderError, match="provider failed"):
        subject.authenticate(request())
    assert subject.state().status is AuthenticationStatus.UNAUTHENTICATED
    subject = service(verifier=FakeVerifier(error=LookupError()))
    with pytest.raises(AuthenticationProviderError, match="verifier failed"):
        subject.authenticate(request())
    assert subject.state().status is AuthenticationStatus.UNAUTHENTICATED


def test_invalid_provider_response_and_verifier_result_are_rejected():
    provider = FakeCredentialProvider()
    provider.response = object()
    with pytest.raises(AuthenticationProviderError, match="invalid response"):
        service(provider=provider).authenticate(request())
    with pytest.raises(AuthenticationProviderError, match="return boolean"):
        service(verifier=FakeVerifier("yes")).authenticate(request())


def test_repeated_authenticate_and_logout_are_rejected_by_default():
    subject = service()
    subject.authenticate(request())
    with pytest.raises(InvalidAuthenticationStateError, match="reauthentication"):
        subject.authenticate(request())
    subject.logout()
    with pytest.raises(InvalidAuthenticationStateError, match="logout requires"):
        subject.logout()


def test_reauthentication_can_be_explicitly_enabled():
    subject = service(allow_reauthentication=True)
    assert subject.authenticate(request()).state.transition_number == 2
    assert subject.authenticate(request()).state.transition_number == 4
    subject.logout()
    assert subject.authenticate(request()).state.status is AuthenticationStatus.AUTHENTICATED
