"""Dedicated append-only JSONL stores for recommendations and later labels."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import json
from pathlib import Path
from typing import Protocol


class ResearchStore(Protocol):
    def append(self, value: object) -> None: ...
    def close(self) -> None: ...


class JsonLinesResearchStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._handle = None

    def append(self, value: object) -> None:
        if self._handle is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a", encoding="utf-8", newline="\n")
        self._handle.write(json.dumps(asdict(value), default=_json, sort_keys=True, separators=(",", ":")) + "\n")
        self._handle.flush()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def _json(value: object) -> str:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"unsupported research value: {type(value).__name__}")


__all__ = ["JsonLinesResearchStore", "ResearchStore"]
