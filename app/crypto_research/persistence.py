"""Dedicated append-only JSONL persistence; never touches equity SQLite."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Protocol


class CryptoResearchStore(Protocol):
    def append(self, record: Mapping[str, object]) -> None: ...
    def close(self) -> None: ...


class CryptoJsonLinesStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._handle = None

    def append(self, record: Mapping[str, object]) -> None:
        if self._handle is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a", encoding="utf-8", newline="\n")
        self._handle.write(
            json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        )
        self._handle.flush()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


__all__ = ["CryptoJsonLinesStore", "CryptoResearchStore"]
