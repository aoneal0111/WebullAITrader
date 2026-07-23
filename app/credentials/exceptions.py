class CredentialError(ValueError):
    """Base error for the broker-neutral credential boundary."""


class InvalidCredentialRequestError(CredentialError):
    pass


class InvalidCredentialResponseError(CredentialError):
    pass


class CredentialProviderError(CredentialError):
    pass
