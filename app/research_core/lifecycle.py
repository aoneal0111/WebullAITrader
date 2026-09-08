"""Bounded, authority-free lifecycle mechanics."""

from __future__ import annotations

from collections import OrderedDict
from hashlib import sha256
import json
from typing import TypeVar


KeyT = TypeVar("KeyT")
ValueT = TypeVar("ValueT")


def semantic_digest(*parts: object) -> str:
    """Return a stable JSON digest for semantic research state."""

    payload = json.dumps(parts, default=str, ensure_ascii=True, separators=(",", ":"))
    return sha256(("atlas-research-semantic-v1|" + payload).encode()).hexdigest()


def remember_bounded(
    values: OrderedDict[KeyT, ValueT], key: KeyT, value: ValueT, *, limit: int
) -> None:
    """Remember newest semantic state with deterministic least-recent eviction."""

    if limit <= 0:
        raise ValueError("retention limit must be positive")
    values[key] = value
    values.move_to_end(key)
    while len(values) > limit:
        values.popitem(last=False)
