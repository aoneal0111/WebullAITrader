from app.authentication import AuthenticationStateSnapshot
from app.session.exceptions import SessionCreationError
from app.session.models import SessionRequest
from app.session.policies import SessionPolicy


def validate_dependencies(authentication_service, policy):
    for operation in ("authenticate", "logout", "state"):
        if not callable(getattr(authentication_service, operation, None)):
            raise SessionCreationError("authentication service has an incompatible interface")
    if not isinstance(policy, SessionPolicy):
        raise SessionCreationError("policy must be SessionPolicy")
    return True


def validate_request(request):
    if not isinstance(request, SessionRequest):
        raise SessionCreationError("request must be SessionRequest")
    return request


def validate_authentication_state(snapshot):
    if not isinstance(snapshot, AuthenticationStateSnapshot):
        raise SessionCreationError("authentication service returned invalid state")
    return snapshot
