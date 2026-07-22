class AuthenticationError(ValueError):
    """Base error for deterministic authentication state handling."""


class InvalidAuthenticationStateError(AuthenticationError):
    pass


class AuthenticationFailedError(AuthenticationError):
    pass


class AuthenticationProviderError(AuthenticationError):
    pass
