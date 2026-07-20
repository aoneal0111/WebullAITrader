import pytest

from app.ai.parser import ResponseParseError, parse_response
from app.ai.response_models import ResponseAction
from app.ai.validator import ResponseValidationError


VALID_RESPONSE = """{
  "action": "BUY",
  "confidence": 94,
  "reason": "Evidence supports the signal, though uncertainty remains.",
  "stop_loss": 123.45,
  "take_profit": 130.25
}"""


def test_parses_valid_response() -> None:
    response = parse_response(VALID_RESPONSE)
    assert response.action is ResponseAction.BUY
    assert response.confidence == 94
    assert response.to_dict()["stop_loss"] == "123.45"


@pytest.mark.parametrize(
    "text",
    [
        f"```json\n{VALID_RESPONSE}\n```",
        f"Analysis:\n{VALID_RESPONSE}",
        "[]",
        "",
    ],
)
def test_rejects_non_json_only_output(text: str) -> None:
    with pytest.raises(ResponseParseError):
        parse_response(text)


def test_rejects_duplicate_fields() -> None:
    text = '{"action":"BUY","action":"SELL","confidence":50,"reason":"x","stop_loss":null,"take_profit":null}'
    with pytest.raises(ResponseParseError, match="duplicate"):
        parse_response(text)


@pytest.mark.parametrize(
    "replacement, message",
    [
        ('"action": "WAIT"', "action"),
        ('"confidence": 101', "confidence"),
        ('"confidence": true', "confidence"),
        ('"reason": ""', "reason"),
        ('"stop_loss": -1', "stop_loss"),
    ],
)
def test_rejects_schema_violations(replacement: str, message: str) -> None:
    field = replacement.split(":", 1)[0]
    lines = [line for line in VALID_RESPONSE.splitlines() if not line.strip().startswith(field)]
    lines.insert(1, f"  {replacement},")
    with pytest.raises(ResponseValidationError, match=message):
        parse_response("\n".join(lines))


def test_rejects_unexpected_fields() -> None:
    text = VALID_RESPONSE[:-2] + ',\n  "execute": true\n}'
    with pytest.raises(ResponseValidationError, match="unexpected"):
        parse_response(text)
