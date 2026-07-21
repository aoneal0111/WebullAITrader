from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.momentum_scanner.models import AssetClass
from app.reference_data.models import ReferenceRecord


@dataclass(frozen=True, slots=True)
class CacheEntry:
    record: ReferenceRecord
    expires_at: datetime


class ReferenceDataCache:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = clock or _utc_now
        self._entries: dict[
            tuple[AssetClass, str],
            CacheEntry,
        ] = {}

    def get(
        self,
        symbol: str,
        asset_class: AssetClass,
    ) -> ReferenceRecord | None:
        key = (asset_class, _normalize_symbol(symbol))
        entry = self._entries.get(key)

        if entry is None:
            return None

        if self._now() >= entry.expires_at:
            self._entries.pop(key, None)
            return None

        return entry.record

    def put(
        self,
        record: ReferenceRecord,
        *,
        ttl: timedelta,
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("ttl must be positive")

        key = (record.asset_class, record.symbol)

        self._entries[key] = CacheEntry(
            record=record,
            expires_at=self._now() + ttl,
        )

    def invalidate(
        self,
        symbol: str,
        asset_class: AssetClass,
    ) -> None:
        key = (asset_class, _normalize_symbol(symbol))
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        self._remove_expired()
        return len(self._entries)

    def _remove_expired(self) -> None:
        now = self._now()

        expired_keys = [
            key
            for key, entry in self._entries.items()
            if now >= entry.expires_at
        ]

        for key in expired_keys:
            self._entries.pop(key, None)

    def _now(self) -> datetime:
        value = self._clock()

        if value.tzinfo is None:
            raise ValueError(
                "cache clock must return a timezone-aware datetime"
            )

        return value


def _normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()

    if not normalized:
        raise ValueError("symbol is required")

    return normalized


def _utc_now() -> datetime:
    return datetime.now(UTC)
