from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from app.ai.response_models import AIResponse
from app.ai.validator import ResponseValidationError, validate_response


class ResponseParseError(ValueError):
    """Raised when response text is not one strict JSON object."""


def parse_response(text: str) -> AIResponse:
    """Parse and validate JSON without attempting to repair model output."""
    if not isinstance(text, str) or not text.strip():
        raise ResponseParseError("response must be non-empty JSON text")
    stripped = text.strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        raise ResponseParseError("response must contain JSON only")
    try:
        value = json.loads(
            stripped,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_non_finite,
            parse_float=Decimal,
        )
    except (json.JSONDecodeError, ResponseParseError) as exc:
        if isinstance(exc, ResponseParseError):
            raise
        raise ResponseParseError("response is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ResponseParseError("response must be a JSON object")
    try:
        return validate_response(value)
    except ResponseValidationError:
        raise


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ResponseParseError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ResponseParseError(f"non-finite JSON number is not allowed: {value}")
