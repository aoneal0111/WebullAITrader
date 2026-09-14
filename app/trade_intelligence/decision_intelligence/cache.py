"""Bounded cache for read-only DI-2 artifact lookups."""

from __future__ import annotations

from collections import OrderedDict
from typing import Generic, TypeVar

T = TypeVar("T")


class LRUCache(Generic[T]):
    def __init__(self, capacity: int = 512) -> None:
        if capacity <= 0:
            raise ValueError("cache capacity must be positive")
        self.capacity = capacity
        self._values: OrderedDict[str, T] = OrderedDict()

    def get(self, key: str) -> T | None:
        value = self._values.get(key)
        if value is not None:
            self._values.move_to_end(key)
        return value

    def put(self, key: str, value: T) -> None:
        self._values[key] = value
        self._values.move_to_end(key)
        while len(self._values) > self.capacity:
            self._values.popitem(last=False)
