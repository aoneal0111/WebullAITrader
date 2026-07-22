import pytest
from app.account_information import *
from app.account_information.validation import validate_dependencies, validate_request
from tests.account_information.helpers import FakeGateway, FakeSessionManager


@pytest.mark.parametrize("args",[(None,FakeGateway(),AccountInformationPolicy()),(FakeSessionManager(),None,AccountInformationPolicy()),(FakeSessionManager(),FakeGateway(),object())])
def test_dependency_validation(args):
    with pytest.raises(AccountInformationDependencyError): validate_dependencies(*args)


def test_request_type_validation():
    with pytest.raises(AccountInformationValidationError): validate_request(object())
