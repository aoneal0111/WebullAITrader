class AccountInformationError(Exception):
    """Base error for account-information orchestration."""


class AccountInformationValidationError(AccountInformationError):
    pass


class AccountInformationDependencyError(AccountInformationError):
    pass


class AccountInformationSerializationError(AccountInformationError):
    pass
