import pytest

from app.credentials import (
    CredentialPolicy, CredentialRequest, InvalidCredentialRequestError,
    InvalidCredentialResponseError, validate_request, validate_response,
)
from tests.credentials.helpers import response


def test_request_and_matching_response_validate():
    request = CredentialRequest("broker", "order-entry", ("user",))
    assert validate_response(request, response(), CredentialPolicy()) == response()


def test_invalid_request_type_rejected():
    with pytest.raises(InvalidCredentialRequestError):
        validate_request(object())


@pytest.mark.parametrize("result", [
    object(),
    response({"wrong": "opaque"}),
    response({"user": "opaque", "extra": "opaque"}),
    response({"user": ""}),
])
def test_invalid_provider_outputs_rejected(result):
    request = CredentialRequest("broker", "order-entry", ("user",))
    with pytest.raises(InvalidCredentialResponseError):
        validate_response(request, result, CredentialPolicy())


def test_broker_and_purpose_must_match():
    request = CredentialRequest("broker", "order-entry", ("user",))
    with pytest.raises(InvalidCredentialResponseError, match="broker identifier"):
        validate_response(request, type(response())("other", "order-entry", {"user": "x"}), CredentialPolicy())
    with pytest.raises(InvalidCredentialResponseError, match="purpose"):
        validate_response(request, type(response())("broker", "other", {"user": "x"}), CredentialPolicy())
