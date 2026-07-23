from app.composition import CompositionRoot, factory, implements_methods
from app.credentials import CredentialPolicy, ValidatingCredentialProvider
from tests.credentials.helpers import FakeCredentialProvider, response


def test_fake_provider_constructed_through_composition_without_retrieval():
    fake = FakeCredentialProvider(response())
    root = CompositionRoot()
    root.register("credential_source", factory(lambda: fake, validator=implements_methods("provide")))
    root.register("credential_provider", factory(
        lambda source: ValidatingCredentialProvider(source, CredentialPolicy(provider_enabled=True)),
        ("credential_source",), implements_methods("provide"),
    ))
    container = root.build()
    assert container.resolve("credential_provider") is not None
    assert fake.requests == []
