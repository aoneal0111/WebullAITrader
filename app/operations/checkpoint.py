from __future__ import annotations

import json
import os
from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.paper_session import PaperTradingSession, paper_session_to_dict
from app.operations.runtime import PaperRuntimeState


class AtomicPaperRuntimeCheckpoint:
    """Write a canonical, replace-atomic runtime snapshot.

    This is an audit/restart checkpoint writer. Restoration remains explicit so
    corrupt or incompatible state is never silently accepted.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def __call__(
        self,
        state: PaperRuntimeState,
        session: PaperTradingSession,
    ) -> None:
        payload = {
            "schema_version": "1",
            "runtime": _json_safe(state),
            "session": paper_session_to_dict(session),
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        return {
            field.name: _json_safe(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, bool, float)):
        return value
    raise TypeError(
        "unsupported runtime checkpoint value: "
        f"{type(value).__module__}.{type(value).__qualname__}"
    )
