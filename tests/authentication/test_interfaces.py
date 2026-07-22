from app.authentication import AuthenticationService, AuthenticationVerifier


def test_service_protocol_operations_are_exact():
    public = {name for name in AuthenticationService.__dict__ if not name.startswith("_")}
    assert public == {"authenticate", "logout", "state"}


def test_verifier_protocol_has_only_verify():
    public = {name for name in AuthenticationVerifier.__dict__ if not name.startswith("_")}
    assert public == {"verify"}
