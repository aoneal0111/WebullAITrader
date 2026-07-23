from dataclasses import FrozenInstanceError
from decimal import Decimal
import pytest

from app.httpx_transport import HTTPXRequestArguments


def test_request_arguments_are_frozen_slotted_and_immutable():
    value = HTTPXRequestArguments("POST", "https://mock.invalid", (("x", "1"),),
                                  (("a", "1"),), {"a": [1]}, True,
                                  Decimal("2"), False)
    assert not hasattr(value, "__dict__")
    assert value.body_value() == {"a": [1]}
    with pytest.raises(FrozenInstanceError):
        value.method = "GET"
    with pytest.raises(TypeError):
        value.body["x"] = True
