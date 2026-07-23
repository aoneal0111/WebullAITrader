class SessionError(ValueError):
    """Base error for deterministic session lifecycle operations."""


class InvalidSessionStateError(SessionError):
    pass


class SessionCreationError(SessionError):
    pass


class SessionReplacementError(SessionError):
    pass
