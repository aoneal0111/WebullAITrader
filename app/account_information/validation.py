from app.account_information.exceptions import AccountInformationDependencyError, AccountInformationValidationError
from app.account_information.models import AccountInformationRequest
from app.account_information.policies import AccountInformationPolicy


def validate_dependencies(session_manager, broker_gateway, policy):
    if session_manager is None or not callable(getattr(session_manager, "state", None)):
        raise AccountInformationDependencyError("session manager must expose state()")
    if broker_gateway is None or not callable(getattr(broker_gateway, "get_account_information", None)):
        raise AccountInformationDependencyError("broker gateway must expose get_account_information(request)")
    if not isinstance(policy, AccountInformationPolicy):
        raise AccountInformationDependencyError("policy must be AccountInformationPolicy")


def validate_request(request):
    if not isinstance(request, AccountInformationRequest):
        raise AccountInformationValidationError("request must be AccountInformationRequest")
    return request
