from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.execution_coordinator import (
    ExecutionCoordinationResult,
)
from app.paper_session.models import PaperTradingSession


def paper_session_to_dict(
    session: PaperTradingSession,
) -> dict[str, Any]:
    result = _json_safe(session)

    if not isinstance(result, dict):
        raise TypeError(
            "session serialization did not produce an object"
        )

    return result


def paper_session_to_json(
    session: PaperTradingSession,
) -> str:
    return json.dumps(
        paper_session_to_dict(session),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _json_safe(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, Decimal):
        return format(value, "f")

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, StrEnum):
        return value.value

    if isinstance(value, ExecutionCoordinationResult):
        return _coordination_summary(value)

    if is_dataclass(value):
        return {
            field.name: _json_safe(
                getattr(value, field.name)
            )
            for field in fields(value)
        }

    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (tuple, list)):
        return [
            _json_safe(item)
            for item in value
        ]

    if isinstance(value, (str, int, bool, float)):
        return value

    raise TypeError(
        "unsupported session serialization value: "
        f"{type(value).__module__}."
        f"{type(value).__qualname__}"
    )


def _coordination_summary(
    value: ExecutionCoordinationResult,
) -> dict[str, Any]:
    return {
        "status": value.status.value,
        "final_stage": value.final_stage.value,
        "strategy_decision": _json_safe(
            value.strategy_decision
        ),
        "order_intent": _json_safe(
            value.order_intent
        ),
        "proposal": _opaque_summary(
            value.proposal
        ),
        "risk_decision": _opaque_summary(
            value.risk_decision
        ),
        "compliance_decision": _opaque_summary(
            value.compliance_decision
        ),
        "execution_result": _execution_summary(
            value.execution_result
        ),
        "trace": _json_safe(value.trace),
    }


def _execution_summary(
    value: Any,
) -> dict[str, Any] | None:
    if value is None:
        return None

    try:
        return {
            "present": True,
            "type": _type_name(value),
            "value": _json_safe(value),
        }
    except TypeError:
        return _opaque_summary(value)


def _opaque_summary(
    value: Any,
) -> dict[str, Any] | None:
    if value is None:
        return None

    return {
        "present": True,
        "type": _type_name(value),
    }


def _type_name(value: Any) -> str:
    value_type = type(value)

    return (
        f"{value_type.__module__}."
        f"{value_type.__qualname__}"
    )
