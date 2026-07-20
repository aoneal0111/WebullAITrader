from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from app.ai.response_models import AIResponse, ResponseAction

RESPONSE_FIELDS = frozenset(
    {"action", "confidence", "reason", "stop_loss", "take_profit"}
)


class ResponseValidationError(ValueError):
    """Raised when a model response violates the expected response contract."""


def validate_response(value: Mapping[str, Any]) -> AIResponse:
    fields = set(value)
    missing = RESPONSE_FIELDS - fields
    extra = fields - RESPONSE_FIELDS
    if missing:
        raise ResponseValidationError(f"missing response fields: {', '.join(sorted(missing))}")
    if extra:
        raise ResponseValidationError(f"unexpected response fields: {', '.join(sorted(extra))}")

    action_raw = value["action"]
    if not isinstance(action_raw, str):
        raise ResponseValidationError("action must be a string")
    try:
        action = ResponseAction(action_raw)
    except ValueError as exc:
        raise ResponseValidationError("action must be BUY, SELL, or HOLD") from exc

    confidence = value["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, int):
        raise ResponseValidationError("confidence must be an integer")
    if not 0 <= confidence <= 100:
        raise ResponseValidationError("confidence must be between 0 and 100")

    reason = value["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise ResponseValidationError("reason must be a non-empty string")

    stop_loss = _optional_positive_decimal(value["stop_loss"], "stop_loss")
    take_profit = _optional_positive_decimal(value["take_profit"], "take_profit")
    return AIResponse(action, confidence, reason.strip(), stop_loss, take_profit)


def _optional_positive_decimal(value: Any, field: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        raise ResponseValidationError(f"{field} must be a number or null")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise ResponseValidationError(f"{field} must be numeric") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ResponseValidationError(f"{field} must be a finite positive number")
    return parsed
