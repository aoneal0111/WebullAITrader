"""Dedicated append-only JSONL persistence for universe admission research."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from enum import Enum
import json
from pathlib import Path

from .models import UniverseAdmissionEvent


class UniverseAdmissionJsonlStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._handle = None

    def append(self, event: UniverseAdmissionEvent) -> None:
        if self._handle is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self._path.open("a", encoding="utf-8", newline="\n")
        self._handle.write(json.dumps(
            asdict(event), default=_json_value, sort_keys=True,
            separators=(",", ":"),
        ) + "\n")
        self._handle.flush()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def _json_value(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"unsupported universe telemetry value: {type(value).__name__}")


__all__ = ["UniverseAdmissionJsonlStore"]
