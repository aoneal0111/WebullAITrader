from app.credentials import CredentialProvider
from tests.credentials.helpers import FakeCredentialProvider


def test_provider_protocol_has_only_provide_operation():
    public = {name for name in CredentialProvider.__dict__ if not name.startswith("_")}
    assert public == {"provide"}
    assert hasattr(FakeCredentialProvider(), "provide")
