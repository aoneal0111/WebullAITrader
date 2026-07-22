class AuthenticationTransportError(ValueError):
    """Base error for authentication transport orchestration."""


class AuthenticationTransportDisabledError(AuthenticationTransportError):
    pass


class AuthenticationTransportDependencyError(AuthenticationTransportError):
    pass


class AuthenticationRequestCreationError(AuthenticationTransportError):
    pass


class AuthenticationRequestExecutionError(AuthenticationTransportError):
    pass


class AuthenticationResponseVerificationError(AuthenticationTransportError):
    pass


class AuthenticationLifecycleError(AuthenticationTransportError):
    pass
