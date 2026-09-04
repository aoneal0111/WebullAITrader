"""Separate append-only persistence for shadow observations."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import json
from pathlib import Path
from typing import Protocol

from .models import EntryOpportunityValueObservation


class ObservationStore(Protocol):
    def append(self, observation: EntryOpportunityValueObservation) -> None: ...

    def close(self) -> None: ...


class JsonLinesObservationStore:
    """Append-only file store intended to be owned by the shadow worker."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._handle = None

    def append(self, observation: EntryOpportunityValueObservation) -> None:
        if self._handle is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self._path.open("a", encoding="utf-8", newline="\n")
        payload = json.dumps(asdict(observation), default=_json_value, sort_keys=True, separators=(",", ":"))
        self._handle.write(payload + "\n")
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
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"unsupported research value: {type(value).__name__}")


__all__ = ["JsonLinesObservationStore", "ObservationStore"]
