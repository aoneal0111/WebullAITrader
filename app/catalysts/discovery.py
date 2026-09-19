from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
from threading import RLock
from typing import Protocol


_TICKER_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{0,4}(?:[.-][A-Z0-9]{1,2})?(?![A-Za-z0-9])"
)
_AMBIGUOUS = frozenset(
    {"A", "AI", "ALL", "ARE", "AS", "AT", "BE", "BY", "CAN", "CEO", "CFO",
     "FOR", "I", "IN", "IT", "NEW", "NO", "ON", "OR", "SEC", "THE", "TO", "US"}
)


class DiscoveryStory(Protocol):
    title: str
    published_at: datetime
    source_url: str
    provider_event_id: str


class DiscoveryStorySource(Protocol):
    name: str

    def recent_stories(
        self, as_of: datetime | None = None
    ) -> tuple[DiscoveryStory, ...]: ...


@dataclass(frozen=True, slots=True)
class CatalystWatchSeed:
    """A research-only request to observe one strictly resolved symbol."""

    symbol: str
    headline: str
    source: str
    source_url: str
    provider_event_id: str
    published_at: datetime
    discovered_at: datetime

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper().replace(".", "-")
        if not symbol or _TICKER_TOKEN.fullmatch(symbol) is None:
            raise ValueError("watch-seed symbol is malformed")
        for name in ("headline", "source", "source_url", "provider_event_id"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"watch-seed {name} is required")
        for name in ("published_at", "discovered_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"watch-seed {name} must be timezone-aware")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "headline", self.headline.strip())
        object.__setattr__(self, "source", self.source.strip().upper())
        object.__setattr__(self, "source_url", self.source_url.strip())
        object.__setattr__(self, "provider_event_id", self.provider_event_id.strip())
        object.__setattr__(self, "published_at", self.published_at.astimezone(UTC))
        object.__setattr__(self, "discovered_at", self.discovered_at.astimezone(UTC))


class CatalystDiscoveryService:
    """Bounded catalyst backfill that cannot authorize execution.

    Only an explicit uppercase ticker in a headline can create a seed, and the
    ticker must also exist in the supplied authoritative symbol directory.
    Company-name guessing and fuzzy symbol inference are intentionally absent.
    """

    def __init__(
        self,
        sources: Iterable[DiscoveryStorySource],
        allowed_symbols_source: Callable[[], Iterable[str]],
        *,
        path: Path | None = None,
        lookback: timedelta = timedelta(hours=24),
        maximum_seeds: int = 100,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sources = tuple(sources)
        if not self._sources:
            raise ValueError("at least one discovery story source is required")
        if not callable(allowed_symbols_source):
            raise TypeError("allowed_symbols_source must be callable")
        if lookback <= timedelta():
            raise ValueError("lookback must be positive")
        if isinstance(maximum_seeds, bool) or maximum_seeds <= 0:
            raise ValueError("maximum_seeds must be positive")
        self._allowed_symbols_source = allowed_symbols_source
        self._path = path
        self._lookback = lookback
        self._maximum_seeds = maximum_seeds
        self._clock = clock
        self._lock = RLock()
        self._seeds: dict[tuple[str, str, str], CatalystWatchSeed] = {}
        self._load()

    def refresh(self, as_of: datetime | None = None) -> tuple[CatalystWatchSeed, ...]:
        now = _aware_utc(as_of if as_of is not None else self._clock())
        cutoff = now - self._lookback
        allowed = {
            normalized
            for value in self._allowed_symbols_source()
            if (normalized := _normalize_directory_symbol(value)) is not None
        }
        discovered: list[CatalystWatchSeed] = []
        for source in self._sources:
            try:
                stories = source.recent_stories(now)
            except Exception:
                continue
            for story in stories:
                published = _story_timestamp(story)
                if published is None or not cutoff <= published <= now + timedelta(minutes=5):
                    continue
                for symbol in _headline_symbols(story.title, allowed):
                    discovered.append(
                        CatalystWatchSeed(
                            symbol=symbol,
                            headline=story.title,
                            source=source.name,
                            source_url=story.source_url,
                            provider_event_id=story.provider_event_id,
                            published_at=published,
                            discovered_at=now,
                        )
                    )
        with self._lock:
            self._seeds = {
                key: seed
                for key, seed in self._seeds.items()
                if seed.published_at >= cutoff
            }
            for seed in discovered:
                self._seeds[(seed.source, seed.provider_event_id, seed.symbol)] = seed
            ordered = _ordered(self._seeds.values())[: self._maximum_seeds]
            self._seeds = {
                (seed.source, seed.provider_event_id, seed.symbol): seed
                for seed in ordered
            }
            self._persist()
            return tuple(ordered)

    def snapshot(self, as_of: datetime | None = None) -> tuple[CatalystWatchSeed, ...]:
        now = _aware_utc(as_of if as_of is not None else self._clock())
        cutoff = now - self._lookback
        with self._lock:
            return tuple(
                seed for seed in _ordered(self._seeds.values())
                if cutoff <= seed.published_at <= now + timedelta(minutes=5)
            )

    def symbols(self, as_of: datetime | None = None) -> tuple[str, ...]:
        return tuple(dict.fromkeys(seed.symbol for seed in self.snapshot(as_of)))

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                return
            loaded: dict[tuple[str, str, str], CatalystWatchSeed] = {}
            for item in payload[: self._maximum_seeds]:
                if not isinstance(item, Mapping):
                    continue
                seed = CatalystWatchSeed(
                    symbol=str(item["symbol"]),
                    headline=str(item["headline"]),
                    source=str(item["source"]),
                    source_url=str(item["source_url"]),
                    provider_event_id=str(item["provider_event_id"]),
                    published_at=datetime.fromisoformat(str(item["published_at"])),
                    discovered_at=datetime.fromisoformat(str(item["discovered_at"])),
                )
                loaded[(seed.source, seed.provider_event_id, seed.symbol)] = seed
            self._seeds = loaded
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            self._seeds = {}

    def _persist(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                **asdict(seed),
                "published_at": seed.published_at.isoformat(),
                "discovered_at": seed.discovered_at.isoformat(),
            }
            for seed in _ordered(self._seeds.values())
        ]
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self._path)


def _headline_symbols(title: str, allowed: set[str]) -> tuple[str, ...]:
    found: list[str] = []
    for token in _TICKER_TOKEN.findall(str(title)):
        normalized = token.replace(".", "-")
        if normalized not in _AMBIGUOUS and normalized in allowed:
            found.append(normalized)
    return tuple(dict.fromkeys(found))


def _normalize_directory_symbol(value: object) -> str | None:
    normalized = str(value).strip().upper().replace(".", "-")
    return normalized if _TICKER_TOKEN.fullmatch(normalized) else None


def _story_timestamp(story: DiscoveryStory) -> datetime | None:
    value = getattr(story, "published_at", None)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    return value.astimezone(UTC)


def _ordered(seeds: Iterable[CatalystWatchSeed]) -> list[CatalystWatchSeed]:
    return sorted(
        seeds,
        key=lambda seed: (
            -int(seed.published_at.timestamp() * 1_000_000),
            seed.symbol,
            seed.source,
            seed.provider_event_id,
        ),
    )


__all__ = [
    "CatalystDiscoveryService",
    "CatalystWatchSeed",
    "DiscoveryStory",
    "DiscoveryStorySource",
]
