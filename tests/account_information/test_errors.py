from app.account_information import *


def test_exception_hierarchy():
    assert issubclass(AccountInformationValidationError,AccountInformationError)
    assert issubclass(AccountInformationDependencyError,AccountInformationError)
    assert issubclass(AccountInformationSerializationError,AccountInformationError)
