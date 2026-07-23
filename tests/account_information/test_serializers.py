import pytest
from app.account_information import *
from tests.account_information.fixtures import request


def test_serializers_are_deterministic_and_safe():
    assert serialize_request(request())==request().to_dict()
    assert serialize_policy(AccountInformationPolicy())==AccountInformationPolicy().to_dict()


def test_serializer_rejects_wrong_type():
    with pytest.raises(AccountInformationSerializationError): serialize_request(object())
