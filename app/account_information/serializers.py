from app.account_information.exceptions import AccountInformationSerializationError
from app.account_information.models import AccountInformationCriteriaResult, AccountInformationRequest, AccountInformationResult, BrokerNeutralAccountInformation
from app.account_information.policies import AccountInformationPolicy


def _serialize(value, expected):
    if not isinstance(value, expected):
        raise AccountInformationSerializationError(f"value must be {expected.__name__}")
    return value.to_dict()


def serialize_request(value): return _serialize(value, AccountInformationRequest)
def serialize_account(value): return _serialize(value, BrokerNeutralAccountInformation)
def serialize_criteria(value): return _serialize(value, AccountInformationCriteriaResult)
def serialize_result(value): return _serialize(value, AccountInformationResult)
def serialize_policy(value): return _serialize(value, AccountInformationPolicy)
