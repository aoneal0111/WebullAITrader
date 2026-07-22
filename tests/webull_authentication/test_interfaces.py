from app.authentication_transport import AuthenticationRequestFactory,AuthenticationResponseVerifier
from app.webull_authentication import WebullAuthenticationRequestFactory,WebullAuthenticationResponseVerifier
def test_exact_interfaces_and_protocol_shapes():
 assert {n for n in WebullAuthenticationRequestFactory.__dict__ if not n.startswith("_")}=={"create"}
 assert {n for n in WebullAuthenticationResponseVerifier.__dict__ if not n.startswith("_")}=={"verify"}
 assert hasattr(AuthenticationRequestFactory,"create") and hasattr(AuthenticationResponseVerifier,"verify")
