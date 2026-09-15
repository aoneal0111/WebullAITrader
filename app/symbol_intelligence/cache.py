"""Bounded, database-free hot cache for immutable intelligence snapshots."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock

from .models import ATTENTION_RANK, AttentionState, SymbolIntelligenceSnapshot


DEFAULT_HOT_SNAPSHOT_MAX = 4_096


@dataclass(frozen=True, slots=True)
class HotCacheMetrics:
    size: int
    capacity: int
    hits: int
    misses: int
    evictions: int
    rejected_puts: int


class HotSnapshotCache:
    """O(1)-average lookup with priority-aware bounded insertion.

    This class has no repository or provider dependency. A miss constructs an
    immutable UNKNOWN/STALE value immediately and performs no I/O.
    """

    def __init__(
        self,
        capacity: int = DEFAULT_HOT_SNAPSHOT_MAX,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if capacity <= 0 or capacity > DEFAULT_HOT_SNAPSHOT_MAX:
            raise ValueError(f"cache capacity must be in 1..{DEFAULT_HOT_SNAPSHOT_MAX}")
        self._capacity = capacity
        self._clock = clock
        self._values: OrderedDict[str, SymbolIntelligenceSnapshot] = OrderedDict()
        self._lock = Lock()
        self._hits = self._misses = self._evictions = self._rejected_puts = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    def hot_snapshot(self, symbol: str) -> SymbolIntelligenceSnapshot:
        key = _symbol(symbol)
        with self._lock:
            value = self._values.get(key)
            if value is not None:
                self._hits += 1
                self._values.move_to_end(key)
                return value
            self._misses += 1
        return SymbolIntelligenceSnapshot.unknown_snapshot(key, as_of=self._clock())

    def put(self, snapshot: SymbolIntelligenceSnapshot) -> bool:
        if not isinstance(snapshot, SymbolIntelligenceSnapshot):
            raise TypeError("snapshot must be SymbolIntelligenceSnapshot")
        key = snapshot.symbol
        with self._lock:
            existing = self._values.get(key)
            if existing is not None:
                if (snapshot.as_of, snapshot.generated_at) < (existing.as_of, existing.generated_at):
                    self._rejected_puts += 1
                    return False
                self._values[key] = snapshot
                self._values.move_to_end(key)
                return True
            if len(self._values) < self._capacity:
                self._values[key] = snapshot
                return True

            existing_values = tuple(self._values.values())
            non_active = tuple(
                value for value in existing_values
                if value.attention_state is not AttentionState.ACTIVE_OPPORTUNITY
            )
            if non_active:
                victim = min((*non_active, snapshot), key=_eviction_key)
                if victim is snapshot:
                    self._rejected_puts += 1
                    return False
            else:
                if snapshot.attention_state is not AttentionState.ACTIVE_OPPORTUNITY:
                    self._rejected_puts += 1
                    return False
                lower_priority_active = tuple(
                    value for value in existing_values
                    if value.priority_score < snapshot.priority_score
                )
                if not lower_priority_active:
                    self._rejected_puts += 1
                    return False
                victim = min(lower_priority_active, key=_eviction_key)

            if victim is snapshot:
                self._rejected_puts += 1
                return False
            self._values.pop(victim.symbol)
            self._values[key] = snapshot
            self._evictions += 1
            return True

    def load(self, snapshots: Iterable[SymbolIntelligenceSnapshot]) -> int:
        accepted = 0
        for snapshot in snapshots:
            accepted += int(self.put(snapshot))
        return accepted

    def remove(self, symbol: str) -> None:
        with self._lock:
            self._values.pop(_symbol(symbol), None)

    def metrics(self) -> HotCacheMetrics:
        with self._lock:
            return HotCacheMetrics(
                len(self._values), self._capacity, self._hits, self._misses,
                self._evictions, self._rejected_puts,
            )

    def __len__(self) -> int:
        with self._lock:
            return len(self._values)


def _eviction_key(snapshot: SymbolIntelligenceSnapshot) -> tuple[object, ...]:
    return (
        ATTENTION_RANK[snapshot.attention_state],
        snapshot.priority_score,
        snapshot.as_of,
        snapshot.generated_at,
        snapshot.symbol,
    )


def _symbol(value: str) -> str:
    normalized = str(value).strip().upper()
    if not normalized or len(normalized) > 32:
        raise ValueError("symbol is required and must be at most 32 characters")
    return normalized


__all__ = [
    "DEFAULT_HOT_SNAPSHOT_MAX", "HotCacheMetrics", "HotSnapshotCache",
]
