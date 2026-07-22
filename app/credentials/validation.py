from app.credentials.exceptions import InvalidCredentialRequestError, InvalidCredentialResponseError
from app.credentials.models import CredentialRequest, CredentialResponse
from app.credentials.policies import CredentialPolicy


def validate_request(request):
    if not isinstance(request, CredentialRequest):
        raise InvalidCredentialRequestError("request must be CredentialRequest")
    return request


def validate_response(request, response, policy):
    validate_request(request)
    if not isinstance(policy, CredentialPolicy):
        raise InvalidCredentialResponseError("policy must be CredentialPolicy")
    if not isinstance(response, CredentialResponse):
        raise InvalidCredentialResponseError("provider must return CredentialResponse")
    if response.broker_identifier != request.broker_identifier:
        raise InvalidCredentialResponseError("response broker identifier does not match request")
    if response.credential_purpose != request.credential_purpose:
        raise InvalidCredentialResponseError("response purpose does not match request")
    required = set(request.required_value_names)
    supplied = set(response.values)
    missing = required - supplied
    if missing:
        raise InvalidCredentialResponseError("response is missing required credential values")
    if not policy.allow_additional_values and supplied - required:
        raise InvalidCredentialResponseError("response contains additional credential values")
    if policy.require_non_empty_values and any(not value for value in response.values.values()):
        raise InvalidCredentialResponseError("credential values must be non-empty")
    return response
