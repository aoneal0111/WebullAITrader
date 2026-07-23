from app.authentication_runtime import AuthenticationRuntime,DeterministicAuthenticationRuntime
def test_exact_interface():
 assert {n for n in AuthenticationRuntime.__dict__ if not n.startswith("_")}=={"authenticate"};assert {n for n in DeterministicAuthenticationRuntime.__dict__ if not n.startswith("_")}=={"authenticate"}
