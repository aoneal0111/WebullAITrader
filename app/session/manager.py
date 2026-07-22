from app.authentication.models import AuthenticationStatus
from app.session.exceptions import InvalidSessionStateError, SessionCreationError, SessionReplacementError
from app.session.models import SessionStatus
from app.session.policies import SessionPolicy
from app.session.state import SessionState
from app.session.validation import validate_authentication_state, validate_dependencies, validate_request


class DeterministicSessionManager:
    def __init__(self, authentication_service, policy: SessionPolicy):
        validate_dependencies(authentication_service, policy)
        self._authentication_service = authentication_service
        self._policy = policy
        self._state = SessionState()

    def _require_authenticated(self):
        try:
            snapshot = self._authentication_service.state()
        except Exception as exc:
            raise SessionCreationError("authentication state unavailable") from exc
        snapshot = validate_authentication_state(snapshot)
        if snapshot.status is not AuthenticationStatus.AUTHENTICATED:
            raise SessionCreationError("authenticated state is required")

    def create(self, request):
        request = validate_request(request)
        self._require_authenticated()
        return self._state.create(request)

    def activate(self):
        return self._state.transition(SessionStatus.ACTIVE)

    def invalidate(self):
        return self._state.transition(SessionStatus.INVALIDATED)

    def replace(self, request):
        request = validate_request(request)
        if not self._policy.allow_replacement:
            raise SessionReplacementError("session replacement is disabled")
        self._require_authenticated()
        return self._state.replace(request)

    def state(self):
        return self._state.snapshot()
