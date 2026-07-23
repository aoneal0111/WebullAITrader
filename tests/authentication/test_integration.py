from app.authentication import AuthenticationPolicy, DeterministicAuthenticationService
from app.composition import CompositionRoot, factory, implements_methods
from tests.authentication.helpers import FakeCredentialProvider, FakeVerifier, request


def test_composition_constructs_and_transitions_fake_only_graph():
    provider, verifier = FakeCredentialProvider(), FakeVerifier()
    root = CompositionRoot()
    root.register("credential_provider", factory(lambda: provider, validator=implements_methods("provide")))
    root.register("authentication_verifier", factory(lambda: verifier, validator=implements_methods("verify")))
    root.register("authentication_service", factory(
        lambda credentials, check: DeterministicAuthenticationService(
            credentials, check, AuthenticationPolicy()),
        ("credential_provider", "authentication_verifier"),
        implements_methods("authenticate", "logout", "state"),
    ))
    service = root.build().resolve("authentication_service")
    assert provider.requests == [] and verifier.calls == []
    service.authenticate(request())
    service.logout()
    assert len(provider.requests) == len(verifier.calls) == 1
