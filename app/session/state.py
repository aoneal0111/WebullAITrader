from app.session.exceptions import InvalidSessionStateError, SessionReplacementError
from app.session.models import Session, SessionRequest, SessionSnapshot, SessionStatus


class SessionState:
    def __init__(self):
        self._status = SessionStatus.NO_SESSION
        self._session = None
        self._replaced = ()
        self._transition_number = 0

    def create(self, request):
        if self._status is not SessionStatus.NO_SESSION:
            raise InvalidSessionStateError("session creation requires NO_SESSION state")
        self._session = Session(request.identifier, request.purpose, SessionStatus.CREATED, request.metadata)
        self._status = SessionStatus.CREATED
        self._transition_number += 1
        return self.snapshot()

    def transition(self, target):
        allowed = {
            SessionStatus.CREATED: SessionStatus.ACTIVE,
            SessionStatus.ACTIVE: SessionStatus.INVALIDATED,
        }
        if not isinstance(target, SessionStatus) or allowed.get(self._status) is not target:
            value = target.value if isinstance(target, SessionStatus) else repr(target)
            raise InvalidSessionStateError(f"invalid session transition: {self._status.value} -> {value}")
        self._status = target
        self._session = Session(
            self._session.identifier, self._session.purpose, target, self._session.metadata)
        self._transition_number += 1
        return self.snapshot()

    def replace(self, request):
        if self._status not in (SessionStatus.CREATED, SessionStatus.ACTIVE, SessionStatus.INVALIDATED):
            raise SessionReplacementError("replacement requires an existing session")
        if request.identifier == self._session.identifier or request.identifier in self._replaced:
            raise SessionReplacementError("replacement identifier must be new")
        previous = self._session.identifier
        self._replaced += (previous,)
        self._session = Session(request.identifier, request.purpose, SessionStatus.CREATED, request.metadata)
        self._status = SessionStatus.CREATED
        self._transition_number += 1
        return self.snapshot()

    def snapshot(self):
        return SessionSnapshot(self._status, self._session, self._replaced,
                               self._transition_number, {"deterministic": True})
