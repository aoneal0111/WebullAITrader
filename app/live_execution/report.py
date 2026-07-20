from __future__ import annotations
import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


def execution_to_json(value: object) -> str:
    return json.dumps(_safe(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def execution_to_text(value: object) -> str:
    safe = _safe(value)
    return "LIVE EXECUTION STATE — BROKER ACTIONS REQUIRE EXPLICIT LIVE AUTHORIZATION\n" + json.dumps(
        safe, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def _safe(value):
    if isinstance(value, Decimal): return format(value, "f")
    if isinstance(value, datetime): return value.isoformat()
    if isinstance(value, Enum): return value.value
    if is_dataclass(value) and not isinstance(value, type): return {field.name: _safe(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict): return {str(key): _safe(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (tuple, list)): return [_safe(item) for item in value]
    return value
