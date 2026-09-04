"""Append-only persistence owned only by the dynamic-discovery worker."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import json
from pathlib import Path
from typing import Protocol

from .models import DynamicMomentumObservation


class DiscoveryStore(Protocol):
    def append(self, observation: DynamicMomentumObservation) -> None: ...
    def close(self) -> None: ...


class JsonLinesDiscoveryStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._handle = None

    def append(self, observation: DynamicMomentumObservation) -> None:
        if self._handle is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self._path.open("a", encoding="utf-8", newline="\n")
        self._handle.write(json.dumps(
            asdict(observation), default=_json_value, sort_keys=True,
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
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"unsupported dynamic-discovery value: {type(value).__name__}")


__all__ = ["DiscoveryStore", "JsonLinesDiscoveryStore"]
