from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.catalysts.discovery import (
    CatalystDiscoveryRuntime,
    CatalystDiscoveryService,
)


NOW = datetime(2026, 9, 19, 8, 50, tzinfo=UTC)


@dataclass(frozen=True)
class Story:
    title: str
    published_at: datetime
    source_url: str
    provider_event_id: str


class Source:
    name = "OFFICIAL_NEWS"

    def __init__(self, stories):
        self._stories = tuple(stories)

    def recent_stories(self, as_of=None):
        return self._stories


def test_startup_backfill_creates_only_strict_authoritative_ticker_seeds(tmp_path):
    service = CatalystDiscoveryService(
        (
            Source(
                (
                    Story(
                        "ABCD wins major FDA clearance",
                        NOW - timedelta(hours=2),
                        "https://example.test/one",
                        "one",
                    ),
                    Story(
                        "abcd reports a contract",
                        NOW - timedelta(hours=1),
                        "https://example.test/two",
                        "two",
                    ),
                    Story(
                        "FAKE announces results",
                        NOW - timedelta(minutes=30),
                        "https://example.test/three",
                        "three",
                    ),
                )
            ),
        ),
        lambda: ("ABCD", "XYZ"),
        path=tmp_path / "watch-seeds.json",
        clock=lambda: NOW,
    )

    seeds = service.refresh()

    assert tuple(seed.symbol for seed in seeds) == ("ABCD",)
    assert service.symbols() == ("ABCD",)


def test_discovery_is_bounded_deduplicated_and_restart_safe(tmp_path):
    stories = tuple(
        Story(
            f"SYM{i} announces a material update",
            NOW - timedelta(minutes=i),
            f"https://example.test/{i}",
            str(i),
        )
        for i in range(5)
    )
    path = tmp_path / "watch-seeds.json"
    source = Source(stories)
    service = CatalystDiscoveryService(
        (source,),
        lambda: tuple(f"SYM{i}" for i in range(5)),
        path=path,
        maximum_seeds=3,
        clock=lambda: NOW,
    )

    first = service.refresh()
    second = service.refresh()
    restored = CatalystDiscoveryService(
        (source,),
        lambda: tuple(f"SYM{i}" for i in range(5)),
        path=path,
        maximum_seeds=3,
        clock=lambda: NOW,
    )

    assert [seed.symbol for seed in first] == ["SYM0", "SYM1", "SYM2"]
    assert second == first
    assert restored.snapshot() == first


def test_stale_and_ambiguous_headline_tokens_do_not_seed():
    service = CatalystDiscoveryService(
        (
            Source(
                (
                    Story(
                        "AI reports results",
                        NOW - timedelta(minutes=5),
                        "https://example.test/ambiguous",
                        "ambiguous",
                    ),
                    Story(
                        "XYZ reports results",
                        NOW - timedelta(hours=25),
                        "https://example.test/stale",
                        "stale",
                    ),
                )
            ),
        ),
        lambda: ("AI", "XYZ"),
        clock=lambda: NOW,
    )

    assert service.refresh() == ()


def test_runtime_refreshes_once_per_interval_and_preserves_watch_set(tmp_path):
    source = Source(
        (
            Story(
                "ABCD announces results",
                NOW - timedelta(minutes=5),
                "https://example.test/one",
                "one",
            ),
        )
    )
    service = CatalystDiscoveryService(
        (source,),
        lambda: ("ABCD",),
        path=tmp_path / "watch-seeds.json",
        clock=lambda: NOW,
    )
    ticks = iter((10.0, 11.0, 400.0))
    runtime = CatalystDiscoveryRuntime(
        service,
        refresh_seconds=300,
        monotonic=lambda: next(ticks),
    )

    assert runtime.symbols() == ("ABCD",)
    assert runtime.symbols() == ("ABCD",)
    assert runtime.symbols() == ("ABCD",)
