from app.authentication.exceptions import AuthenticationProviderError
from app.authentication.interfaces import AuthenticationVerifier
from app.authentication.models import AuthenticationRequest
from app.authentication.policies import AuthenticationPolicy


def validate_dependencies(provider, verifier, policy):
    if not callable(getattr(provider, "provide", None)):
        raise AuthenticationProviderError("credential provider must implement provide")
    if not callable(getattr(verifier, "verify", None)):
        raise AuthenticationProviderError("authentication verifier must implement verify")
    if not isinstance(policy, AuthenticationPolicy):
        raise AuthenticationProviderError("policy must be AuthenticationPolicy")
    return True


def validate_authentication_request(request):
    if not isinstance(request, AuthenticationRequest):
        raise AuthenticationProviderError("request must be AuthenticationRequest")
    return request
