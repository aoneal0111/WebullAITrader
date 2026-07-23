from dataclasses import FrozenInstanceError
import pytest

from app.credentials import (
    CredentialRequest, CredentialResponse, InvalidCredentialRequestError,
    InvalidCredentialResponseError,
)


def test_request_is_frozen_slotted_immutable_and_round_trips():
    request = CredentialRequest("broker", "order-entry", ("user",), {"caller": "outer"})
    assert CredentialRequest.from_dict(request.to_dict()) == request
    assert not hasattr(request, "__dict__")
    with pytest.raises(FrozenInstanceError):
        request.credential_purpose = "changed"
    with pytest.raises(TypeError):
        request.metadata["x"] = True


def test_response_is_frozen_slotted_immutable_and_round_trips():
    response = CredentialResponse("broker", "order-entry", {"user": "opaque"})
    assert CredentialResponse.from_dict(response.to_dict()) == response
    assert not hasattr(response, "__dict__")
    with pytest.raises(TypeError):
        response.values["user"] = "changed"


@pytest.mark.parametrize("args", [
    ("", "purpose", ("value",)),
    ("broker", "", ("value",)),
    ("broker", "purpose", ()),
    ("broker", "purpose", ("value", "value")),
])
def test_invalid_requests_are_rejected(args):
    with pytest.raises(InvalidCredentialRequestError):
        CredentialRequest(*args)


@pytest.mark.parametrize("values", [{}, {"": "value"}, {"name": 1}])
def test_invalid_responses_are_rejected(values):
    with pytest.raises(InvalidCredentialResponseError):
        CredentialResponse("broker", "purpose", values)
